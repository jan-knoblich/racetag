#!/usr/bin/env python3
"""Gekoppelte Reserve-Nummern mit Kategorie-Platzhaltern benennen.

Jeder Fahrer OHNE Namen bekommt "Nachmeldung <Kategorie>" — damit ist am
Anmeldetisch und in den Standings klar, zu welcher Wertung eine Reserve-
Plakette gehört. Echte Namen werden NIE überschrieben; beim Sign-on ersetzt
der Fahrer-Editor den Platzhalter. Läufer-Puffer werden über den
Nummernblock beschriftet (10 km / 5 km / U18).

Usage: python3 platzhalter_namen.py [slots…]   (Default: alle belegten)
"""
import csv
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_SLOTS = ["0900", "1120", "1210", "1300", "1500", "1600", "1615",
                 "1715", "1815", "1930", "2000"]
SLOT_RACE = {
    "0900": "Lauf", "1120": "U15", "1210": "U17m", "1300": "Masters2",
    "1500": "leicht", "1600": "Quali", "1615": "Frauen", "1715": "mittel",
    "1815": "schwer", "1930": "FLINTA", "2000": "A Final",
}
LABEL = {
    "u15m": "U15m", "u17w": "U17w", "u17m": "U17m",
    "masters_2": "Masters 2", "masters_3": "Masters 3", "masters_4": "Masters 4",
    "junioren": "Junioren", "jedermann_leicht": "Jedermann leicht",
    "frauen_elite": "Frauen Lizenz", "jedefrau": "Jedefrau",
    "juniorinnen": "Juniorinnen", "jedermann_mittel": "Jedermann mittel",
    "jedermann_schwer": "Jedermann schwer", "fixed_gear_men": "Fixed Gear",
    "flinta": "FLINTA",
}


def lauf_label(bib):
    if 101 <= bib <= 211:
        return "Lauf 10km"
    if 301 <= bib <= 420:
        return "Lauf 5km"
    if 501 <= bib <= 530:
        return "Lauf U18"
    return "Lauf"


def req(base, method, path, body=None):
    r = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r, timeout=10) as x:
        return json.load(x)


def discover_base():
    out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                         capture_output=True, text=True, check=False).stdout
    ports = {l.rsplit(":", 1)[-1].split(" ")[0].replace("(LISTEN)", "").strip()
             for l in out.splitlines() if "127.0.0.1:" in l}
    for port in sorted(ports):
        base = f"http://127.0.0.1:{port}"
        try:
            if "active_race_id" in req(base, "GET", "/races"):
                return base
        except Exception:
            continue
    return None


def main():
    slots = sys.argv[1:] or DEFAULT_SLOTS
    base = discover_base()
    if not base:
        sys.exit("App läuft nicht?")
    races = req(base, "GET", "/races")
    orig = races["active_race_id"]

    for slot in slots:
        frag = SLOT_RACE.get(slot)
        race = next((r for r in races["items"]
                     if frag and frag.lower() in r["name"].lower()), None)
        if race is None:
            continue
        kat = {}
        src = next((HERE / "zuweisung").glob(f"{slot}-*anmeldeliste.csv"), None)
        if src:
            with open(src, encoding="utf-8-sig") as f:
                for r in csv.DictReader(f, delimiter=";"):
                    if r.get("nummer", "").strip():
                        kat[r["nummer"].strip()] = r.get("kategorie", "").strip()

        req(base, "POST", f"/races/{race['id']}/activate")
        riders = req(base, "GET", "/riders")["items"]
        updated = 0
        for r in riders:
            if r["name"].strip():
                continue  # echte Namen nie anfassen
            k = kat.get(r["bib"], "")
            if slot == "0900" and r["bib"].isdigit():
                label = lauf_label(int(r["bib"]))
            else:
                label = LABEL.get(k, "") or "Reserve"
            req(base, "POST", "/riders",
                {"tag_id": r["tag_id"], "bib": r["bib"],
                 "name": f"Nachmeldung {label}"})
            updated += 1
        if updated:
            print(f"{race['name']}: {updated} Platzhalter gesetzt")

    req(base, "POST", f"/races/{orig}/activate")


if __name__ == "__main__":
    main()
