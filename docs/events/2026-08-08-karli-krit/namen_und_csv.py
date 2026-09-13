#!/usr/bin/env python3
"""Vorab-Namen (zuweisung/) in die Rennen schreiben + Tag-CSV pro Rennen.

Für jeden angegebenen Slot: Namen aus zuweisung/<slot>-anmeldeliste.csv per
Upsert ins passende Rennen (nur echte Änderungen), dann alle gekoppelten Tags
als ~/Downloads/tags-<slot>.csv (tag_id;bib;name;kategorie — import-tauglich,
dient als Backup). Meldet Läufer/Fahrer, deren Nummer nicht gekoppelt ist.

Usage: python3 namen_und_csv.py 1120 1210 1300 …   (Default: alle Rad-Slots
       außer 1930/2000)
"""
import csv
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_SLOTS = ["1120", "1210", "1300", "1500", "1600", "1615", "1715", "1815"]
SLOT_RACE = {
    "0900": "Lauf", "1030": "Kids", "1120": "U15", "1210": "U17m",
    "1300": "Masters2", "1500": "leicht", "1600": "Quali", "1615": "Frauen",
    "1715": "mittel", "1815": "schwer", "1930": "FLINTA", "2000": "A Final",
}


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
        src = next((HERE / "zuweisung").glob(f"{slot}-*anmeldeliste.csv"), None)
        if race is None or src is None:
            print(f"{slot}: Rennen oder Anmeldeliste nicht gefunden — übersprungen")
            continue

        zw = {}
        with open(src, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f, delimiter=";"):
                n = r.get("nummer", "").strip()
                if n:
                    zw[n] = (r.get("name", "").strip(), r.get("kategorie", "").strip())

        req(base, "POST", f"/races/{race['id']}/activate")
        riders = req(base, "GET", "/riders")["items"]
        updated = 0
        for r in riders:
            name = zw.get(r["bib"], ("", ""))[0]
            if name and r["name"].strip() != name:
                req(base, "POST", "/riders",
                    {"tag_id": r["tag_id"], "bib": r["bib"], "name": name})
                updated += 1
        riders = req(base, "GET", "/riders")["items"]

        slug = src.name.replace("-anmeldeliste.csv", "")
        outp = Path.home() / f"Downloads/tags-{slug}.csv"
        rows = sorted(riders, key=lambda r: int(r["bib"]) if r["bib"].isdigit() else 9999)
        with open(outp, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";", lineterminator="\n")
            w.writerow(["tag_id", "bib", "name", "kategorie (Import ignoriert)"])
            for r in rows:
                name, kat = zw.get(r["bib"], (r["name"], ""))
                w.writerow([r["tag_id"], r["bib"], name or r["name"], kat])

        coupled = {r["bib"] for r in riders}
        fehlt = [(n, v[0]) for n, v in zw.items() if v[0] and n not in coupled]
        mit = sum(1 for r in rows if (zw.get(r["bib"], (r["name"],))[0] or r["name"]))
        print(f"{race['name']}: {updated} Namen geschrieben, "
              f"{len(rows)} Tags -> {outp.name} ({mit} mit Namen)")
        for n, name in sorted(fehlt, key=lambda x: int(x[0])):
            print(f"  ⚠ Nr. {n} ({name}) NICHT gekoppelt!")

    req(base, "POST", f"/races/{orig}/activate")


if __name__ == "__main__":
    main()
