#!/usr/bin/env python3
"""Create the 11 Karli-Krit races (2026-08-08) in a running racetag app.

Usage: python3 create_races.py [http://127.0.0.1:PORT]
Without an argument the script auto-discovers the app's port by probing
local listeners for GET /races. Idempotent: races that already exist (same
name) are skipped, so it is safe to re-run.

Times are local (Leipzig, UTC+2) → scheduled_at stored as UTC.
Timed races (Jedermann etc.) get duration_s + final_laps=3 (F2 mode);
total_laps 999 so the lap counter can never finish them early. final_laps
is adjustable on race day in the app header.
"""
import json
import subprocess
import sys
import urllib.request

RACES = [
    # (name, total_laps, duration_s or None, scheduled_at UTC)
    # Lauf: 5-km- und 10-km-Läufer gemischt (Nummernblöcke trennen die
    # Wertung); per_rider + 10 Runden — 10-km-Finisher werden 'finished',
    # 5-km-Läufer bleiben bei 5 Runden stehen und werden über den
    # Nummernblock ausgewertet.
    ("09:00 Lauf 5/10km", 10, None, "2026-08-08T07:00:00.000Z", "per_rider"),
    ("10:30 Kids Stadtmeisterschaft", 5, None, "2026-08-08T08:30:00.000Z"),
    ("11:20 U15+U17w (20 Rd)", 20, None, "2026-08-08T09:20:00.000Z"),
    ("12:10 U17m+Masters4 (32 Rd)", 32, None, "2026-08-08T10:10:00.000Z"),
    ("13:00 Masters2/3+Junioren (45 Rd)", 45, None, "2026-08-08T11:00:00.000Z"),
    ("15:00 Jedermann leicht (45 min)", 999, 2700, "2026-08-08T13:00:00.000Z"),
    ("16:00 Fixed Gear Quali (5 Rd)", 5, None, "2026-08-08T14:00:00.000Z"),
    ("16:15 Frauen Elite+U19w+Jedefrau (30 Rd)", 30, None, "2026-08-08T14:15:00.000Z"),
    ("17:15 Jedermann mittel (45 min)", 999, 2700, "2026-08-08T15:15:00.000Z"),
    ("18:15 Jedermann schwer (60 min)", 999, 3600, "2026-08-08T16:15:00.000Z"),
    ("19:30 Fixed Gear B + FLINTA (25 min)", 999, 1500, "2026-08-08T17:30:00.000Z"),
    ("20:00 Fixed Gear A Final (30 min)", 999, 1800, "2026-08-08T18:00:00.000Z"),
]
FINAL_LAPS = 3
SNAPSHOT_INTERVAL_S = 120


def _get(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
        return json.load(r)


def _send(base, method, path, body):
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def discover_base():
    out = subprocess.run(
        ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
        capture_output=True, text=True, check=False,
    ).stdout
    ports = set()
    for line in out.splitlines():
        if "127.0.0.1:" in line:
            ports.add(line.rsplit(":", 1)[-1].split(" ")[0].replace("(LISTEN)", "").strip())
    for port in sorted(ports):
        base = f"http://127.0.0.1:{port}"
        try:
            data = _get(base, "/races")
            if isinstance(data, dict) and "active_race_id" in data:
                return base
        except Exception:
            continue
    return None


def main():
    base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else discover_base()
    if not base:
        sys.exit("racetag-Backend nicht gefunden — App starten oder URL angeben.")
    print(f"Backend: {base}")

    existing = {r["name"] for r in _get(base, "/races")["items"]}
    for entry in RACES:
        name, laps, duration, sched = entry[:4]
        finish_mode = entry[4] if len(entry) > 4 else "leader"
        if name in existing:
            print(f"skip   {name} (existiert)")
            continue
        race = _send(base, "POST", "/races", {
            "name": name,
            "total_laps": laps,
            "scheduled_at": sched,
            "snapshot_interval_s": SNAPSHOT_INTERVAL_S,
            "finish_mode": finish_mode,
        })
        if duration:
            _send(base, "PATCH", f"/races/{race['id']}", {
                "duration_s": duration,
                "final_laps": FINAL_LAPS,
            })
        fmt = f"{duration // 60} min + {FINAL_LAPS} Rd." if duration else f"{laps} Rd."
        print(f"create {name} — {fmt}")

    print("\nFertig. Nächster Schritt: Master-CSV (Export tags nach der "
          "Koppel-Session) in JEDES Rennen importieren (Race wählen → Import CSV).")


if __name__ == "__main__":
    main()
