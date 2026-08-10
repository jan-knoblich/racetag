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
]
# U18 (Block 500-599 + Nachmeldung 196) wird nach MELDEKATEGORIE gewertet
# (lauf_[mw]_u18_[5|10]km aus zuweisung/0900-…-anmeldeliste.csv): gleiche
# Strecke wie die Erwachsenen, 5 km = 7. Überfahrt, 10 km = 13. Überfahrt.
MIN_GAP_S = 15.0  # wie das Backend-Cooldown: Überfahrten dichter dran = 1 Pass
# Läufer schaffen die ~770-m-Runde nie unter 100 s — alles darunter (nach der
# ersten Überfahrt!) ist eine Phantom-Lesung (Linie doppelt gequert o. ä.).
# Die ERSTE Überfahrt (Startsegment ~1/5 Runde) bleibt beim 15-s-Gate.
LAUF_MIN_LAP_S = 100.0

# Raceday 08.08.: Papiere 395-416/511-514 fehlten → vier 5-km-Läufer laufen
# mit 10-km-Puffernummern. Diese Nummern werden im 5-km-Block gewertet!
# 193 Till Winkel · 194 Christian Zoch · 195 Raphael Schmiedel
# (196 Lenn Wilke ist lt. Meldung U18 → landet über die Kategorie im U18-Block)
BLOCK_OVERRIDE = {193: "5km Erwachsene", 194: "5km Erwachsene",
                  195: "5km Erwachsene"}


def load_u18_bloecke() -> dict[int, tuple[str, int]]:
    """bib -> (Blocklabel, Ziel-Überfahrten) für alle U18-Meldungen."""
    src = next((Path(__file__).parent / "zuweisung").glob("0900-*anmeldeliste.csv"), None)
    out: dict[int, tuple[str, int]] = {}
    if src is None:
        return out
    with open(src, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            k = (r.get("kategorie") or "").strip()
            n = (r.get("nummer") or "").strip()
            if "u18" not in k or not n.isdigit():
                continue
            dist = "5km" if "5km" in k else "10km"
            sex = "männlich" if "_m_" in k else "weiblich"
            out[int(n)] = (f"{dist} U18 {sex}", 7 if dist == "5km" else 13)
    return out


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
    u18 = load_u18_bloecke()

    def block_von(bib: int):
        if bib in u18:
            return u18[bib]
        ov = BLOCK_OVERRIDE.get(bib)
        for label, lo, hi, target in BLOCKS:
            if ov is not None:
                if ov == label:
                    return label, target
            elif lo <= bib <= hi:
                return label, target
        return None

    gruppen: dict[str, dict] = {}
    for label, _, _, target in BLOCKS:
        gruppen[label] = {"target": target, "riders": []}
    for label, target in u18.values():
        gruppen.setdefault(label, {"target": target, "riders": []})

    for tag_id, lst in passes.items():
        rider = riders.get(tag_id)
        if rider is None:
            continue
        try:
            bib = int(rider["bib"])
        except (TypeError, ValueError):
            continue
        blk = block_von(bib)
        if blk is not None:
            gruppen[blk[0]]["riders"].append((bib, rider, lst))

    for label, g in gruppen.items():
        target = g["target"]
        results = []
        anomalies = []
        for bib, rider, lst in g["riders"]:
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
                continue
            # Unvollständig: Defizit nur werten, wenn es lückenlos durch
            # nachweisbare Fehllesungen erklärt ist (~2x-/~3x-Runden) —
            # dann sind das Nachzügler mit voller Distanz, kein DNF.
            defizit = n - len(lst)
            erklaert, luecken = 0, []
            if med:
                for i, s in enumerate(segs[1:], start=2):
                    ratio = s / med
                    if 1.7 <= ratio < 2.55:
                        erklaert += 1
                        luecken.append(f"Runde {i} = {ratio:.1f}x Median")
                    elif 2.55 <= ratio < 3.55:
                        erklaert += 2
                        luecken.append(f"Runde {i} = {ratio:.1f}x Median (2 Lesungen)")
            if med and 0 < defizit <= erklaert:
                mehrzahl = "en" if defizit > 1 else ""
                entry["zeit_s"] = (lst[-1] - start).total_seconds()
                entry["zeit"] = fmt_hms(entry["zeit_s"])
                hinweise.append(
                    f"KORRIGIERT: {defizit} Lesung{mehrzahl} unterwegs verpasst "
                    f"({'; '.join(luecken)}) — Ziel = letzte Überfahrt")
                entry["hinweis"] = "; ".join(hinweise)
                results.append(entry)
            else:
                if defizit == 1 and not luecken and len(lst) >= 3:
                    # Alle Runden lückenlos, nur die allerletzte Überfahrt
                    # fehlt: Ziellesung verpasst ODER auf der Schlussrunde
                    # ausgestiegen — ohne Beleg nicht wertbar.
                    hinweise.append(
                        f"{len(lst)} lückenlose Überfahrten, nur die letzte fehlt "
                        "— Ziellesung verpasst oder Ausstieg auf der Schlussrunde")
                    entry["grenzfall"] = True
                else:
                    hinweise.append(f"nur {len(lst)} von {target} Überfahrten")
                entry["letzte_s"] = (lst[-1] - start).total_seconds()
                entry["hinweis"] = "; ".join(hinweise)
                anomalies.append(entry)

        results.sort(key=lambda e: e["zeit_s"])
        sieger_s = results[0]["zeit_s"] if results else None
        for e in anomalies:
            letzte = fmt_hms(e["letzte_s"])
            if e.get("grenzfall"):
                e["hinweis"] += f" — letzte Überfahrt {letzte}"
            elif sieger_s is not None and e["letzte_s"] > sieger_s:
                e["hinweis"] += (f" — letzte Überfahrt {letzte} (nach Siegerzeit): "
                                 "volle Distanz nicht belegbar")
            else:
                e["hinweis"] += f" — letzte Überfahrt {letzte}: vermutlich DNF"
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
