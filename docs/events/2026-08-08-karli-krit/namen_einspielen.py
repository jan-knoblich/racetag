#!/usr/bin/env python3
"""Eriks finale Startliste (Nummer↔Name) ins AKTIVE Rennen einspielen.

Für den 15-Minuten-Fall: Erik gibt die komplette Liste, dieses Skript setzt
die Namen über die laufende App (Port wird automatisch gefunden) — kein
Master-CSV-Gefummel, kein Reader. Nur Nummern, die im aktiven Rennen als
Fahrer existieren (= gekoppelte Plaketten), werden aktualisiert; alles andere
wird gemeldet statt still verschluckt.

Listenformat: CSV/Text, zwei Spalten pro Zeile (Nummer und Name, Reihenfolge
egal, Trennzeichen ; , oder Tab, Kopfzeile erlaubt). Beispiel:
    12;Max Mustermann
    Erika Beispiel, 34

Usage: python3 namen_einspielen.py liste.csv [--backend http://127.0.0.1:PORT]
       --dry-run zeigt nur, was passieren würde.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.request


def _get(base, path):
    with urllib.request.urlopen(f"{base}{path}", timeout=5) as r:
        return json.load(r)


def _post(base, path, body):
    req = urllib.request.Request(
        f"{base}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.load(r)


def discover_base():
    out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                         capture_output=True, text=True, check=False).stdout
    ports = {line.rsplit(":", 1)[-1].split(" ")[0].replace("(LISTEN)", "").strip()
             for line in out.splitlines() if "127.0.0.1:" in line}
    for port in sorted(ports):
        base = f"http://127.0.0.1:{port}"
        try:
            if "active_race_id" in _get(base, "/races"):
                return base
        except Exception:
            continue
    return None


def parse_liste(path):
    pairs = []
    skipped = []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip().lstrip("﻿")
            if not line:
                continue
            cells = [c.strip() for c in re.split(r"[;\t]|,(?![^,]*,)", line) if c.strip()]
            nums = [c for c in cells if c.isdigit()]
            texts = [c for c in cells if not c.isdigit()]
            if len(nums) == 1 and texts:
                pairs.append((nums[0], " ".join(texts)))
            elif lineno == 1:
                continue  # Kopfzeile
            else:
                skipped.append(f"Zeile {lineno}: {line!r}")
    return pairs, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("liste")
    ap.add_argument("--backend", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    base = args.backend or discover_base()
    if not base:
        sys.exit("racetag-Backend nicht gefunden — App starten oder --backend angeben.")
    races = _get(base, "/races")
    active = next(r for r in races["items"] if r["id"] == races["active_race_id"])
    print(f"Aktives Rennen: {active['name']}")

    riders = _get(base, "/riders")["items"]
    by_bib = {r["bib"]: r for r in riders}

    pairs, skipped = parse_liste(args.liste)
    print(f"Liste: {len(pairs)} Zeilen erkannt, {len(skipped)} unlesbar")
    for s in skipped:
        print(f"  ⚠ {s}")

    updated = same = missing = 0
    for bib, name in pairs:
        rider = by_bib.get(bib)
        if rider is None:
            print(f"  ⚠ Nr. {bib} ({name}) ist im aktiven Rennen NICHT gekoppelt!")
            missing += 1
            continue
        if rider["name"].strip() == name:
            same += 1
            continue
        old = rider["name"].strip()
        print(f"  Nr. {bib}: {old or '—'} -> {name}")
        if not args.dry_run:
            _post(base, "/riders", {"tag_id": rider["tag_id"], "bib": bib,
                                    "name": name})
        updated += 1

    verb = "würden aktualisiert" if args.dry_run else "aktualisiert"
    print(f"\n{updated} {verb}, {same} stimmten schon, {missing} ohne Plakette.")


if __name__ == "__main__":
    main()
