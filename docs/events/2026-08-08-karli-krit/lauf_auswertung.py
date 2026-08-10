#!/usr/bin/env python3
"""Karli-Lauf-Auswertung: 5 km und 10 km aus EINEM racetag-Rennen.

Warum: Der Lauf läuft als ein Rennen mit total_laps=10. 5-km-Läufer sind bei
5 Runden fertig, werden in racetag aber nie "finished" — und wer nach dem
Ziel nochmal über die Linie schlendert, bekäme eine Phantom-Runde. Dieses
Skript ist dagegen immun: Es liest den Audit-Trail (tag_events, read-only)
und nimmt pro Läufer exakt die N-te gezählte Überfahrt seines Nummernblocks
(5 km → 5., 10 km → 10.). total_laps in der App NIEMALS umstellen (M3/M4)!

Usage: python3 lauf_auswertung.py [--race "09:00 Lauf"] [--db PFAD]
Schreibt ergebnis-lauf-<block>.csv neben dieses Skript + Anomalie-Report.
"""
import argparse
import csv
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

# Nummernblöcke: (Label, von, bis, Ziel-Runden) — an Eriks Ansage anpassen!
BLOCKS = [
    # inkl. +20%-Puffer-Verbreiterung (siehe nummern_zuweisung.py)
    ("10km Erwachsene", 100, 220, 13),
    ("5km Erwachsene", 300, 420, 7),
    ("5km-10km U18", 500, 599, None),  # None = Ziel unklar, nur Rohliste
]
MIN_GAP_S = 15.0  # wie das Backend-Cooldown: Überfahrten dichter dran = 1 Pass
# Läufer schaffen die ~770-m-Runde nie unter 100 s — alles darunter (nach der
# ersten Überfahrt!) ist eine Phantom-Lesung (Linie doppelt gequert o. ä.).
# Die ERSTE Überfahrt (Startsegment ~1/5 Runde) bleibt beim 15-s-Gate.
LAUF_MIN_LAP_S = 100.0

# Raceday 08.08.: Papiere 395-416/511-514 fehlten → vier 5-km-Läufer laufen
# mit 10-km-Puffernummern. Diese Nummern werden im 5-km-Block gewertet!
# 193 Till Winkel · 194 Christian Zoch · 195 Raphael Schmiedel ·
# 196 Lenn Wilke (U18!)
BLOCK_OVERRIDE = {193: "5km Erwachsene", 194: "5km Erwachsene",
                  195: "5km Erwachsene", 196: "5km Erwachsene"}


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fmt_hms(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--race", default="Lauf", help="Teil des Rennnamens")
    ap.add_argument("--db", default=str(Path.home() / ".racetag/data/racetag.db"))
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    race = con.execute(
        "SELECT * FROM races WHERE name LIKE ? ORDER BY created_at DESC LIMIT 1;",
        (f"%{args.race}%",),
    ).fetchone()
    if race is None:
        raise SystemExit(f"Kein Rennen mit '{args.race}' im Namen gefunden.")
    if not race["started_at"]:
        raise SystemExit(f"Rennen '{race['name']}' wurde nie gestartet.")
    start = parse_ts(race["started_at"])
    print(f"Rennen: {race['name']}  Start: {race['started_at']}")

    riders = {r["tag_id"]: r for r in con.execute(
        "SELECT tag_id, bib, name FROM riders WHERE race_id = ?;", (race["id"],))}

    # Gezählte Überfahrten pro Tag: chronologisch, Mindestabstand wie im
    # Backend (erste Überfahrt zusätzlich >= MIN_GAP_S nach dem Start).
    passes: dict[str, list[datetime]] = {}
    rows = con.execute(
        "SELECT tag_id, timestamp FROM tag_events "
        "WHERE race_id = ? AND event_type = 'arrive' ORDER BY timestamp, id;",
        (race["id"],))
    phantome = []
    for row in rows:
        ts = parse_ts(row["timestamp"])
        if ts < start:
            continue
        lst = passes.setdefault(row["tag_id"], [])
        anchor = lst[-1] if lst else start
        gate = MIN_GAP_S if not lst else LAUF_MIN_LAP_S
        delta = (ts - anchor).total_seconds()
        if delta < gate:
            if delta >= MIN_GAP_S:
                phantome.append((row["tag_id"], ts, delta))
            continue
        lst.append(ts)
    for tag_id, ts, delta in phantome:
        r = riders.get(tag_id)
        if r is None:
            continue  # unregistrierte Dauerleser (Orga-Tags) nicht fluten
        print(f"  ⚠ Phantom-Überfahrt verworfen: Nr. {r['bib']} {r['name']} um "
              f"{ts.strftime('%H:%M:%S')} ({delta:.0f} s nach der vorigen)")

    outdir = Path(__file__).parent
    for label, lo, hi, target in BLOCKS:
        results = []
        anomalies = []
        for tag_id, lst in passes.items():
            rider = riders.get(tag_id)
            if rider is None:
                continue
            try:
                bib = int(rider["bib"])
            except (TypeError, ValueError):
                continue
            override = BLOCK_OVERRIDE.get(bib)
            if override is not None:
                if override != label:
                    continue  # zählt in seinem Override-Block, nicht hier
            elif not (lo <= bib <= hi):
                continue
            segs = [(lst[i] - (lst[i - 1] if i else start)).total_seconds()
                    for i in range(len(lst))]
            hinweise = []
            n = target or len(lst)
            # Startüberfahrt verpasst? Das Startsegment (~1/5 Runde) muss
            # STRIKT schneller sein als jede Folgerunde. Sonst ist die erste
            # gezählte Überfahrt bereits Runde 1: virtuelle Startrunde vorne
            # draufrechnen = Wertung auf Überfahrt N-1 verschieben.
            if target and len(segs) >= 3 and segs[0] >= min(segs[1:]):
                n = target - 1
                hinweise.append(
                    f"Startüberfahrt verpasst (Seg. 1 = {fmt_hms(segs[0])} nicht "
                    f"schnellste) — Wertung auf Überfahrt {n}")
            med = statistics.median(segs[1:]) if len(segs) > 2 else None
            entry = {"bib": bib, "name": rider["name"], "ueberfahrten": len(lst)}
            if len(lst) >= n:
                entry["zeit_s"] = (lst[n - 1] - start).total_seconds()
                entry["zeit"] = fmt_hms(entry["zeit_s"])
                if target and len(lst) != target and not hinweise:
                    hinweise.append(f"{len(lst)} statt {target} Überfahrten (Extra ignoriert)")
                entry["hinweis"] = "; ".join(hinweise)
                results.append(entry)
            elif (target and med and len(lst) == n - 1
                  and max(segs[1:]) >= 1.7 * med):
                # Finisher mit genau EINER unterwegs verpassten Lesung: die
                # letzte Überfahrt IST das Ziel (Beleg: eine ~2x-Runde).
                k = segs.index(max(segs[1:]))
                entry["zeit_s"] = (lst[-1] - start).total_seconds()
                entry["zeit"] = fmt_hms(entry["zeit_s"])
                hinweise.append(
                    f"KORRIGIERT: 1 Lesung unterwegs verpasst (Runde {k + 1} = "
                    f"{max(segs[1:]) / med:.1f}x Median) — Ziel = letzte Überfahrt")
                entry["hinweis"] = "; ".join(hinweise)
                results.append(entry)
            else:
                hinweise.append(f"nur {len(lst)} von {target} Überfahrten — Lesung fehlt? DNF?")
                entry["hinweis"] = "; ".join(hinweise)
                anomalies.append(entry)

        results.sort(key=lambda e: e["zeit_s"])
        slug = label.lower().replace(" ", "-").replace("ä", "ae").replace("ü", "ue")
        path = outdir / f"ergebnis-lauf-{slug}.csv"
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";", lineterminator="\n")
            w.writerow(["platz", "nummer", "name", "zeit", "ueberfahrten", "hinweis"])
            for i, e in enumerate(results, 1):
                w.writerow([i, e["bib"], e["name"], e["zeit"],
                            e["ueberfahrten"], e.get("hinweis", "")])
            for e in sorted(anomalies, key=lambda x: x["bib"]):
                w.writerow(["-", e["bib"], e["name"], "", e["ueberfahrten"], e["hinweis"]])
        print(f"{label}: {len(results)} gewertet, {len(anomalies)} unvollständig -> {path.name}")


if __name__ == "__main__":
    main()
