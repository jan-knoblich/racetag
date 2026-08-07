#!/usr/bin/env python3
"""Prüflauf vor dem Raceday: Soll/Ist-Abgleich aller Kopplungen + Formate.

Prüft gegen die laufende App (Port-Autodiscovery):
  1. pro Rennen: gekoppelte Nummern vs. Soll-Zirkel — fehlend (= noch zu
     koppeln), überzählig (FEHLER), doppelt vergebene Nummern (FEHLER)
  2. Objekt-Identität über Rennen hinweg:
     - Lauf-Papier und Rad-Plakette mit gleicher Nummer müssen VERSCHIEDENE
       Tags sein (sonst wurde ein Objekt in zwei Rennen gekoppelt)
     - Fixed-Gear-Plaketten (411-446) müssen in Quali/B-Final/A-Final den
       GLEICHEN Tag tragen (Plakette bleibt beim Fahrer)
  3. Rennformate (Runden/Dauer/Modus) gegen den Tagesplan
  4. kein Rennen versehentlich gestartet/beendet
  5. Default race enthält keine Rest-Kopplungen mehr

Exit 0 = keine FEHLER (offene Kopplungen sind kein Fehler, nur Status).
Während des Prüflaufs kurz nicht koppeln (wechselt die aktiven Rennen durch,
stellt am Ende das ursprüngliche wieder her).
"""
import json
import subprocess
import sys
import urllib.request

# Soll-Zirkel pro Rennen (Namensfragment → Nummernbereiche inklusiv)
EXPECTED = {
    "09:00 Lauf": [(101, 211), (301, 416), (501, 514)],
    "10:30 Kids": [],
    "11:20": [(81, 90), (301, 304)],
    "12:10": [(181, 193), (316, 326)],
    "13:00": [(281, 294), (331, 343), (401, 410)],
    "15:00": [(1, 50)],
    "16:00": [(411, 436)],
    "16:15": [(351, 366), (371, 381), (391, 395)],
    "17:15": [(101, 175)],
    "18:15": [(201, 240)],
    "19:30": [(411, 436), (441, 446)],
    "20:00": [(411, 436)],
}
# (total_laps, duration_s oder None, finish_mode)
FORMATS = {
    "09:00 Lauf": (10, None, "per_rider"),
    "11:20": (20, None, "leader"),
    "12:10": (32, None, "leader"),
    "13:00": (45, None, "leader"),
    "15:00": (999, 2700, "leader"),
    "16:00": (5, None, "leader"),
    "16:15": (30, None, "leader"),
    "17:15": (999, 2700, "leader"),
    "18:15": (999, 3600, "leader"),
    "19:30": (999, 1500, "leader"),
    "20:00": (999, 1800, "leader"),
}
FG_RANGE = (411, 446)


def _req(base, method, path, body=None):
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


def discover_base():
    out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                         capture_output=True, text=True, check=False).stdout
    ports = {l.rsplit(":", 1)[-1].split(" ")[0].replace("(LISTEN)", "").strip()
             for l in out.splitlines() if "127.0.0.1:" in l}
    for port in sorted(ports):
        base = f"http://127.0.0.1:{port}"
        try:
            if "active_race_id" in _req(base, "GET", "/races"):
                return base
        except Exception:
            continue
    return None


def in_ranges(n, ranges):
    return any(lo <= n <= hi for lo, hi in ranges)


def main():
    base = discover_base()
    if not base:
        sys.exit("App läuft nicht?")
    resp = _req(base, "GET", "/races")
    races = resp["items"]
    original_active = resp["active_race_id"]

    errors, notes = [], []
    riders_by_race = {}

    for frag in EXPECTED:
        race = next((r for r in races if frag.lower() in r["name"].lower()), None)
        if race is None:
            errors.append(f"Rennen fehlt in der App: {frag}")
            continue
        _req(base, "POST", f"/races/{race['id']}/activate")
        items = _req(base, "GET", "/riders")["items"]
        riders_by_race[frag] = items
        ranges = EXPECTED[frag]
        soll = {n for lo, hi in ranges for n in range(lo, hi + 1)}

        bibs = {}
        bad_bib = 0
        for r in items:
            if not r["bib"].isdigit():
                bad_bib += 1
                continue
            bibs.setdefault(int(r["bib"]), []).append(r["tag_id"])
        doppelt = {b: t for b, t in bibs.items() if len(t) > 1}
        ist = set(bibs)
        fehlt = soll - ist
        fremd = ist - soll

        status = f"{len(ist & soll):>3}/{len(soll)} gekoppelt"
        if fehlt:
            status += f", {len(fehlt)} offen"
        line = f"{race['name']}: {status}"
        print(line)

        for b, tags in sorted(doppelt.items()):
            errors.append(f"{race['name']}: Nr. {b} DOPPELT gekoppelt "
                          f"({', '.join('…' + t[-8:] for t in tags)})")
        if fremd:
            errors.append(f"{race['name']}: Nummern außerhalb des Zirkels: "
                          f"{sorted(fremd)}")
        if bad_bib:
            errors.append(f"{race['name']}: {bad_bib} Fahrer mit nicht-numerischer Nummer")

        # Format + Flags
        fmt = FORMATS.get(frag)
        if fmt:
            laps, dur, mode = fmt
            if race["total_laps"] != laps:
                errors.append(f"{race['name']}: total_laps {race['total_laps']} statt {laps}")
            if (race.get("duration_s") or None) != dur:
                errors.append(f"{race['name']}: duration_s {race.get('duration_s')} statt {dur}")
            if race.get("finish_mode") != mode:
                errors.append(f"{race['name']}: finish_mode {race.get('finish_mode')} statt {mode}")
        if race["started"] or race["ended"]:
            errors.append(f"{race['name']}: bereits gestartet/beendet?!")

    # Cross-Checks: Lauf-Papier vs. Rad-Plakette = verschiedene Tags
    lauf = {int(r["bib"]): r["tag_id"] for r in riders_by_race.get("09:00 Lauf", [])
            if r["bib"].isdigit()}
    for frag, items in riders_by_race.items():
        if frag == "09:00 Lauf":
            continue
        for r in items:
            if not r["bib"].isdigit():
                continue
            b = int(r["bib"])
            if b in lauf and lauf[b] == r["tag_id"]:
                errors.append(f"Nr. {b}: GLEICHER Tag im Lauf und in {frag} — "
                              f"ein Objekt in zwei Rennen gekoppelt!")

    # Fixed Gear: gleiche Plakette = gleicher Tag in allen drei FG-Rennen
    fg_races = ["16:00", "19:30", "20:00"]
    fg_maps = {f: {int(r["bib"]): r["tag_id"] for r in riders_by_race.get(f, [])
                   if r["bib"].isdigit() and FG_RANGE[0] <= int(r["bib"]) <= FG_RANGE[1]}
               for f in fg_races}
    for b in range(FG_RANGE[0], FG_RANGE[1] + 1):
        tags = {f: m[b] for f, m in fg_maps.items() if b in m}
        if len(set(tags.values())) > 1:
            errors.append(f"Fixed Gear Nr. {b}: unterschiedliche Tags über die "
                          f"FG-Rennen: { {f: '…' + t[-8:] for f, t in tags.items()} }")

    # Default race muss leer sein
    default = next((r for r in races if r["name"] == "Default race"), None)
    if default:
        _req(base, "POST", f"/races/{default['id']}/activate")
        n = _req(base, "GET", "/riders")["count"]
        if n:
            notes.append(f"Default race enthält noch {n} Kopplungen (sollte 0 sein)")

    _req(base, "POST", f"/races/{original_active}/activate")

    print()
    for n in notes:
        print(f"HINWEIS: {n}")
    if errors:
        print(f"\n{len(errors)} FEHLER:")
        for e in errors:
            print(f"  ✘ {e}")
        sys.exit(1)
    print("KEINE strukturellen Fehler — offene Kopplungen oben sind nur Rest-Arbeit.")


if __name__ == "__main__":
    main()
