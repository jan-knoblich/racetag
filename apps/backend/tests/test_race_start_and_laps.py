"""Start time after the fact, per-race lap cooldown, no laps after the finish.

Field report Hubland run 2026-09-27: the start button was pressed 5.5 minutes
after the gun, a global 8 s cooldown counted single crossings several times,
and runners who crossed the mat again after finishing showed up to 12 laps.
These tests pin the three fixes, and the last one replays that race day.
"""
import importlib
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

BASE = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture()
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("RACETAG_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RACETAG_API_KEY", raising=False)
    import app as app_module
    importlib.reload(app_module)
    with TestClient(app_module.app) as client:
        yield client, app_module


def _arrive(client, tag, when, antenna=1):
    res = client.post("/events/tag/batch", json={"events": [{
        "source": "test", "reader_ip": "192.168.178.22", "timestamp": _iso(when),
        "event_type": "arrive", "tag_id": tag, "antenna": antenna, "rssi": -50,
    }]})
    assert res.status_code == 200, res.text


def _laps(client):
    return {p["tag_id"]: p for p in client.get("/classification").json()["standings"]}


def _active_race_id(client):
    return client.get("/race").json()["id"]


# ---------------------------------------------------------------------------
# No laps after the rider's own finish
# ---------------------------------------------------------------------------

def test_crossings_after_the_finish_do_not_add_laps(app_client):
    client, app_module = app_client
    race_id = _active_race_id(client)
    client.patch(f"/races/{race_id}", json={"total_laps": 2, "finish_mode": "per_rider",
                                            "min_pass_interval_s": 60})
    client.post("/riders", json={"tag_id": "AAA1", "bib": "7", "name": "Läufer"})
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})

    _arrive(client, "AAA1", BASE + timedelta(minutes=12))
    _arrive(client, "AAA1", BASE + timedelta(minutes=24))      # finish
    _arrive(client, "AAA1", BASE + timedelta(minutes=27))      # walking back
    _arrive(client, "AAA1", BASE + timedelta(minutes=40))      # cool-down lap

    p = _laps(client)["AAA1"]
    assert p["laps"] == 2
    assert p["finished"] is True
    assert p["post_finish_passes"] == 2
    assert p["finish_time"] == _iso(BASE + timedelta(minutes=24))
    assert p["last_pass_time"] == _iso(BASE + timedelta(minutes=24))
    assert p["total_time_ms"] == 24 * 60 * 1000


def test_manual_lap_on_a_finished_rider_is_refused_clearly(app_client):
    client, app_module = app_client
    race_id = _active_race_id(client)
    client.patch(f"/races/{race_id}", json={"total_laps": 1, "finish_mode": "per_rider"})
    client.post("/riders", json={"tag_id": "AAA1", "bib": "7", "name": "Läufer"})
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    _arrive(client, "AAA1", BASE + timedelta(minutes=12))

    res = client.post("/riders/AAA1/laps", json={})
    assert res.status_code == 409
    assert "already finished" in res.json()["detail"]
    assert _laps(client)["AAA1"]["laps"] == 1


# ---------------------------------------------------------------------------
# Per-race cooldown
# ---------------------------------------------------------------------------

def test_race_cooldown_overrides_the_global_default(app_client):
    client, app_module = app_client
    client.patch("/config", json={"min_lap_interval_s": 8})
    race_id = _active_race_id(client)

    summary = client.get(f"/races/{race_id}").json()
    assert summary["min_pass_interval_s"] is None
    assert summary["effective_min_pass_interval_s"] == 8

    client.patch(f"/races/{race_id}", json={"min_pass_interval_s": 360})
    summary = client.get(f"/races/{race_id}").json()
    assert summary["min_pass_interval_s"] == 360
    assert summary["effective_min_pass_interval_s"] == 360
    assert app_module.race.min_pass_interval_s == 360

    # The Settings value is only the default: it must not clobber the race's own.
    client.patch("/config", json={"min_lap_interval_s": 20})
    assert app_module.race.min_pass_interval_s == 360

    # Explicit null clears the race's own value, back to the default.
    client.patch(f"/races/{race_id}", json={"min_pass_interval_s": None})
    summary = client.get(f"/races/{race_id}").json()
    assert summary["min_pass_interval_s"] is None
    assert summary["effective_min_pass_interval_s"] == 20
    assert app_module.race.min_pass_interval_s == 20


def test_new_race_takes_its_cooldown_and_keeps_it_after_a_restart(app_client, monkeypatch):
    client, app_module = app_client
    created = client.post("/races", json={"name": "10 km", "total_laps": 4,
                                           "finish_mode": "per_rider",
                                           "min_pass_interval_s": 300}).json()
    assert created["min_pass_interval_s"] == 300
    client.post(f"/races/{created['id']}/activate")
    assert app_module.race.min_pass_interval_s == 300

    importlib.reload(app_module)
    with TestClient(app_module.app) as fresh:
        assert app_module.race.min_pass_interval_s == 300
        assert fresh.get(f"/races/{created['id']}").json()["effective_min_pass_interval_s"] == 300


def test_cooldown_drops_double_reads_and_the_crossing_right_after_the_start(app_client):
    """A timing point 200 m after the start: that first crossing is within the
    cooldown of the start and must not count as a lap."""
    client, app_module = app_client
    race_id = _active_race_id(client)
    client.patch(f"/races/{race_id}", json={"total_laps": 2, "finish_mode": "per_rider",
                                            "min_pass_interval_s": 240})
    client.post("/riders", json={"tag_id": "AAA1", "bib": "7", "name": "Läufer"})
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})

    _arrive(client, "AAA1", BASE + timedelta(seconds=45))                 # 200 m mark
    _arrive(client, "AAA1", BASE + timedelta(minutes=12))                 # lap 1
    _arrive(client, "AAA1", BASE + timedelta(minutes=12, seconds=20))     # still in the zone
    _arrive(client, "AAA1", BASE + timedelta(minutes=24))                 # lap 2 = finish

    p = _laps(client)["AAA1"]
    assert p["laps"] == 2
    assert p["finish_time"] == _iso(BASE + timedelta(minutes=24))


def test_raising_the_cooldown_on_a_running_race_recounts_from_the_readings(app_client):
    client, app_module = app_client
    race_id = _active_race_id(client)
    client.patch(f"/races/{race_id}", json={"total_laps": 10, "finish_mode": "per_rider",
                                            "min_pass_interval_s": 8})
    client.post("/riders", json={"tag_id": "AAA1", "bib": "7", "name": "Läufer"})
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    for t in (12 * 60, 12 * 60 + 15, 12 * 60 + 30, 24 * 60):
        _arrive(client, "AAA1", BASE + timedelta(seconds=t))
    assert _laps(client)["AAA1"]["laps"] == 4          # double reads counted

    client.patch(f"/races/{race_id}", json={"min_pass_interval_s": 300})
    assert _laps(client)["AAA1"]["laps"] == 2


# ---------------------------------------------------------------------------
# Start time after the fact
# ---------------------------------------------------------------------------

def test_moving_the_start_earlier_recovers_passes_before_the_button(app_client):
    client, app_module = app_client
    race_id = _active_race_id(client)
    client.patch(f"/races/{race_id}", json={"total_laps": 3, "finish_mode": "per_rider",
                                            "min_pass_interval_s": 240})
    client.post("/riders", json={"tag_id": "AAA1", "bib": "7", "name": "Läufer"})
    _arrive(client, "AAA1", BASE + timedelta(minutes=12))
    # Button pressed late: 15 minutes after the gun.
    client.patch("/race/start-time", json={"started_at": _iso(BASE + timedelta(minutes=15))})
    _arrive(client, "AAA1", BASE + timedelta(minutes=24))
    assert _laps(client)["AAA1"]["laps"] == 1

    res = client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    assert res.status_code == 200
    assert res.json()["previous_started_at"] == _iso(BASE + timedelta(minutes=15))
    p = _laps(client)["AAA1"]
    assert p["laps"] == 2
    assert p["total_time_ms"] == 24 * 60 * 1000

    # The corrected start survives a restart (the legacy meta key agrees).
    importlib.reload(app_module)
    with TestClient(app_module.app) as fresh:
        assert fresh.get("/race").json()["started_at"] == _iso(BASE)
        assert _laps(fresh)["AAA1"]["laps"] == 2


def test_start_time_validation(app_client):
    client, app_module = app_client
    assert client.patch("/race/start-time", json={"started_at": "gestern"}).status_code == 422
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    assert client.patch("/race/start-time", json={"started_at": _iso(future)}).status_code == 422

    client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    client.post("/race/end")
    later = datetime.now(timezone.utc) + timedelta(seconds=2)
    res = client.patch("/race/start-time", json={"started_at": _iso(later)})
    assert res.status_code == 422


def test_start_time_starts_a_race_that_was_not_started(app_client):
    client, app_module = app_client
    assert client.get("/race").json()["started"] is False
    res = client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    assert res.status_code == 200
    body = client.get("/race").json()
    assert body["started"] is True
    assert body["started_at"] == _iso(BASE)
    assert res.json()["previous_started_at"] is None


# ---------------------------------------------------------------------------
# Mass-start detection
# ---------------------------------------------------------------------------

def _register(client, n):
    for i in range(n):
        client.post("/riders", json={"tag_id": f"T{i:03d}", "bib": str(i + 1), "name": f"L{i}"})


def test_mass_start_before_the_button_is_detected(app_client):
    client, app_module = app_client
    _register(client, 20)
    gun = BASE
    for i in range(16):                       # 16 of 20 over the mat within 90 s
        _arrive(client, f"T{i:03d}", gun + timedelta(seconds=40 + i * 5))
    pressed = gun + timedelta(minutes=5, seconds=30)

    res = client.get("/race/start-candidate", params={"before": _iso(pressed)}).json()
    assert res["detected"] is True
    assert res["first_reading"] == _iso(gun + timedelta(seconds=40))
    assert res["riders_in_burst"] == 16
    assert res["registered"] == 20


def test_a_coupling_session_is_not_mistaken_for_a_start(app_client):
    client, app_module = app_client
    _register(client, 40)
    for i in range(12):                       # tags waved one by one, 20 s apart
        _arrive(client, f"T{i:03d}", BASE + timedelta(seconds=i * 20))
    res = client.get("/race/start-candidate",
                     params={"before": _iso(BASE + timedelta(minutes=6))}).json()
    assert res["detected"] is False
    assert res["threshold"] == 12             # 30 % of 40


def test_start_candidate_defaults_to_the_race_start(app_client):
    client, app_module = app_client
    _register(client, 10)
    for i in range(8):
        _arrive(client, f"T{i:03d}", BASE + timedelta(seconds=30 + i))
    client.patch("/race/start-time", json={"started_at": _iso(BASE + timedelta(minutes=5))})
    res = client.get("/race/start-candidate").json()
    assert res["reference"] == _iso(BASE + timedelta(minutes=5))
    assert res["detected"] is True

    # Once corrected to the gun, nothing precedes the start any more.
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    assert client.get("/race/start-candidate").json()["detected"] is False


# ---------------------------------------------------------------------------
# The Hubland day, replayed
# ---------------------------------------------------------------------------

def test_hubland_replay_gives_the_right_result_once_the_start_is_fixed(app_client):
    """Gun at BASE; timing mat 200 m in; 5 km = 2 laps of ~2.4 km; the button
    was pressed 5.5 minutes late; two runners crossed again after finishing."""
    client, app_module = app_client
    race_id = _active_race_id(client)
    client.patch(f"/races/{race_id}", json={"total_laps": 2, "finish_mode": "per_rider",
                                            "min_pass_interval_s": 240})
    runners = {  # tag: (200 m mark, lap seconds)
        "R1": (40, 8 * 60 + 5),
        "R2": (45, 10 * 60),
        "R3": (70, 13 * 60 + 30),
    }
    for n, tag in enumerate(runners, 1):
        client.post("/riders", json={"tag_id": tag, "bib": str(n), "name": tag})
    for tag, (mark, lap) in runners.items():
        _arrive(client, tag, BASE + timedelta(seconds=mark))
        for k in (1, 2):
            _arrive(client, tag, BASE + timedelta(seconds=mark + k * lap))
    # Walks back through the zone 5 minutes after finishing (outside the
    # 4-minute cooldown, so it is a real second crossing, not a double read).
    _arrive(client, "R1", BASE + timedelta(seconds=40 + 2 * (8 * 60 + 5) + 300))
    _arrive(client, "R2", BASE + timedelta(seconds=45 + 3 * 10 * 60))             # extra lap

    # What happened on the day: start pressed 5.5 minutes after the gun.
    client.patch("/race/start-time", json={"started_at": _iso(BASE + timedelta(minutes=5, seconds=30))})
    detected = client.get("/race/start-candidate").json()
    assert detected["detected"] is True
    assert detected["first_reading"] == _iso(BASE + timedelta(seconds=40))

    # The operator accepts the detected time and pulls it back by the ~40 s
    # the field needs to reach the 200 m mat.
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})

    standings = client.get("/classification").json()["standings"]
    order = [p["tag_id"] for p in standings]
    assert order == ["R1", "R2", "R3"]
    by_tag = {p["tag_id"]: p for p in standings}
    for tag, (mark, lap) in runners.items():
        p = by_tag[tag]
        assert p["laps"] == 2, tag
        assert p["finished"] is True, tag
        assert p["total_time_ms"] == (mark + 2 * lap) * 1000, tag
    assert by_tag["R1"]["post_finish_passes"] == 1
    assert by_tag["R2"]["post_finish_passes"] == 1


def test_readings_export_says_why_a_reading_did_not_count(app_client):
    import io
    from openpyxl import load_workbook

    client, app_module = app_client
    race_id = _active_race_id(client)
    client.patch(f"/races/{race_id}", json={"total_laps": 1, "finish_mode": "per_rider",
                                            "min_pass_interval_s": 240})
    client.post("/riders", json={"tag_id": "AAA1", "bib": "7", "name": "Läufer"})
    _arrive(client, "AAA1", BASE - timedelta(minutes=10))                  # before the start
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    _arrive(client, "AAA1", BASE + timedelta(seconds=45))                  # 200 m mark
    _arrive(client, "AAA1", BASE + timedelta(minutes=12))                  # lap 1 = finish
    _arrive(client, "AAA1", BASE + timedelta(minutes=12, seconds=30))      # double read
    _arrive(client, "AAA1", BASE + timedelta(minutes=20))                  # after the finish
    _arrive(client, "ZZZ9", BASE + timedelta(minutes=13))                  # unknown tag

    wb = load_workbook(io.BytesIO(client.get("/readings.xlsx").content))
    rows = list(wb["Lesungen"].iter_rows(values_only=True))
    cols = {name: i for i, name in enumerate(rows[0])}
    reasons = [(r[cols["Tag-ID"]], r[cols["Gewertet"]], r[cols["Warum nicht gewertet"]]) for r in rows[1:]]
    assert reasons == [
        ("AAA1", "nein", "vor dem Rennstart"),
        ("AAA1", "nein", "zu kurz nach dem Start (Mindestabstand)"),
        ("AAA1", "ja", None),
        ("AAA1", "nein", "zu kurz nach der letzten Runde (Mindestabstand)"),
        ("ZZZ9", "nein", "Tag nicht gekoppelt"),
        ("AAA1", "nein", "nach dem eigenen Zieleinlauf"),
    ]


# ---------------------------------------------------------------------------
# Unknown tags and late coupling (points 3 and 5 of the field report)
# ---------------------------------------------------------------------------

def test_unknown_tags_are_listed_during_the_race(app_client):
    client, app_module = app_client
    client.post("/riders", json={"tag_id": "AAA1", "bib": "7", "name": "Gekoppelt"})
    _arrive(client, "OLD1", BASE - timedelta(minutes=20))                 # before the start
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    _arrive(client, "AAA1", BASE + timedelta(minutes=12))
    _arrive(client, "ZZZ9", BASE + timedelta(minutes=12))
    _arrive(client, "ZZZ9", BASE + timedelta(minutes=24), antenna=2)
    _arrive(client, "YYY8", BASE + timedelta(minutes=13))

    res = client.get("/race/unknown-tags").json()
    assert res["scope"] == "race"
    assert res["count"] == 2
    assert [i["tag_id"] for i in res["items"]] == ["ZZZ9", "YYY8"]
    zzz = res["items"][0]
    assert zzz["reads"] == 2
    assert zzz["antennas"] == [1, 2]
    assert zzz["known_as"] is None


def test_unknown_tag_suggests_its_number_from_another_race(app_client):
    client, app_module = app_client
    client.post("/riders", json={"tag_id": "ZZZ9", "bib": "42", "name": "Aus Lauf 1"})
    second = client.post("/races", json={"name": "Lauf 2", "total_laps": 2,
                                         "finish_mode": "per_rider"}).json()
    client.post(f"/races/{second['id']}/activate")
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    _arrive(client, "ZZZ9", BASE + timedelta(minutes=12))

    item = client.get("/race/unknown-tags").json()["items"][0]
    assert item["known_as"] == {"bib": "42", "name": "Aus Lauf 1", "race_name": "Default race"}


def test_before_the_start_unknown_tags_cover_the_last_half_hour(app_client):
    client, app_module = app_client
    now = datetime.now(timezone.utc)
    _arrive(client, "OLD1", now - timedelta(minutes=50))
    _arrive(client, "NEW1", now - timedelta(minutes=5))
    res = client.get("/race/unknown-tags").json()
    assert res["scope"] == "recent"
    assert [i["tag_id"] for i in res["items"]] == ["NEW1"]


def test_coupling_a_tag_after_it_ran_counts_its_laps(app_client):
    """Seven Hubland runners ran uncoupled. Coupling them afterwards must
    bring their laps and finish times back from the stored readings."""
    client, app_module = app_client
    race_id = _active_race_id(client)
    client.patch(f"/races/{race_id}", json={"total_laps": 2, "finish_mode": "per_rider",
                                            "min_pass_interval_s": 240})
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    for minutes in (12, 24):
        _arrive(client, "ANON", BASE + timedelta(minutes=minutes))
    assert "ANON" not in _laps(client)

    client.post("/riders", json={"tag_id": "ANON", "bib": "99", "name": "Nachgekoppelt",
                                 "recount_past_reads": True})
    p = _laps(client)["ANON"]
    assert p["laps"] == 2
    assert p["finished"] is True
    assert p["total_time_ms"] == 24 * 60 * 1000
    assert client.get("/race/unknown-tags").json()["count"] == 0


def test_coupling_without_recount_keeps_earlier_reads_out(app_client):
    """Holding a spare tag to the antenna to couple a late entry mid-race must
    not turn that read into a lap."""
    client, app_module = app_client
    client.patch("/race/start-time", json={"started_at": _iso(BASE)})
    _arrive(client, "SPARE", BASE + timedelta(minutes=20))          # waved at the antenna
    client.post("/riders", json={"tag_id": "SPARE", "bib": "150", "name": "Nachmeldung"})
    p = _laps(client).get("SPARE")
    assert p is None or p["laps"] == 0
