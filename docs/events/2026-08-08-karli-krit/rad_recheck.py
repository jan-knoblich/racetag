#!/usr/bin/env python3
"""Rad-Recheck: nicht selbst ausgelöste Fehler in den großen Rennen finden.

Zwei Verdachtsmuster pro Rennen (read-only auf der DB):
  1. Fahrer mit -1/-2 Runden im Endstand, deren Rundenzeiten eine ~2x-Lücke
     enthalten -> vermutlich NICHT überrundet, sondern Lesung(en) verpasst.
  2. Fahrer, deren letzte Überfahrt auffällig spät liegt (deutlich nach dem
     Vorfahrer gleicher Rundenzahl) -> Ziel-Lesung verpasst, spätere
     Überfahrt als Ziel gezählt (Zeit zu spät).

Usage: python3 rad_recheck.py ["Masters2" "mittel" …]  (Default: die großen)
"""
import argparse
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

MIN_GAP_S = 15.0
DEFAULT = ["U17m", "Masters2", "leicht", "Frauen", "mittel", "schwer"]


def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def lokal(dt):
    return dt.astimezone().strftime("%H:%M:%S")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rennen", nargs="*", default=DEFAULT)
    ap.add_argument("--db", default=str(Path.home() / ".racetag/data/racetag.db"))
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    for frag in args.rennen:
        race = con.execute(
            "SELECT * FROM races WHERE name LIKE ? AND started=1 "
            "ORDER BY started_at DESC LIMIT 1", (f"%{frag}%",)).fetchone()
        if race is None:
            print(f"⚠ kein Rennen zu {frag!r}")
            continue
        start = parse_ts(race["started_at"])
        ende = parse_ts(race["ended_at"]) if race["ended_at"] else None
        riders = {r["tag_id"]: r for r in con.execute(
            "SELECT tag_id, bib, name FROM riders WHERE race_id=?", (race["id"],))}

        passes = {}
        for row in con.execute(
                "SELECT tag_id,timestamp FROM tag_events WHERE race_id=? AND "
                "event_type='arrive' ORDER BY timestamp,id", (race["id"],)):
            ts = parse_ts(row["timestamp"])
            if ts < start or (ende and ts > ende):
                continue
            lst = passes.setdefault(row["tag_id"], [])
            if (ts - (lst[-1] if lst else start)).total_seconds() < MIN_GAP_S:
                continue
            lst.append(ts)

        # Nur registrierte Fahrer mit plausiblen Rundenzeiten (keine
        # Orga-Dauerleser) in die Wertungsanalyse.
        feld = {}
        for tag, lst in passes.items():
            r = riders.get(tag)
            if r is None or len(lst) < 3:
                continue
            segs = [(lst[i] - (lst[i - 1] if i else start)).total_seconds()
                    for i in range(len(lst))]
            med = statistics.median(segs[1:])
            if med < 45:
                continue
            feld[tag] = (r, lst, segs, med)
        if not feld:
            print(f"== {race['name']}: keine analysierbaren Fahrer")
            continue

        leader_laps = max(len(v[1]) for v in feld.values())
        print(f"== {race['name']} — {len(feld)} Fahrer, Sieger {leader_laps} Überfahrten")

        for tag, (r, lst, segs, med) in sorted(
                feld.items(), key=lambda kv: -len(kv[1][1])):
            behind = leader_laps - len(lst)
            luecken = [(i + 1, s / med, lst[i]) for i, s in enumerate(segs)
                       if i > 0 and s >= 1.7 * med]
            # Muster 1: "überrundet", aber Lücken erklären den Rückstand
            if 1 <= behind <= 2 and luecken:
                erkl = sum(round(ratio) - 1 for _, ratio, _ in luecken)
                det = "; ".join(f"Runde {i} = {ratio:.1f}x um {lokal(ts)}"
                                for i, ratio, ts in luecken)
                print(f"  ⚠ Nr. {r['bib']} {r['name']}: -{behind} Runde(n), aber "
                      f"{det} -> erklärt ~{erkl} verpasste Lesung(en)"
                      + (" — VERMUTLICH VOLLE DISTANZ" if erkl >= behind else ""))
            # Muster 2: letzte Überfahrt auffällig spät (nach eigener Runde
            # >1.5x Median als Schlussrunde)
            if segs[-1] >= 1.7 * med and behind == 0:
                print(f"  ⚠ Nr. {r['bib']} {r['name']}: Schlussrunde "
                      f"{segs[-1] / med:.1f}x Median ({lokal(lst[-1])}) — "
                      f"Ziel-Lesung evtl. verpasst, Zeit zu spät?")


if __name__ == "__main__":
    main()
