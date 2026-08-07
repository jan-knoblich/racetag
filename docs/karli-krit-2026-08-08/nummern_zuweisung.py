#!/usr/bin/env python3
"""Nummern-Zuweisung vorab: Meldeliste × Nummernzirkel → Startnummern.

Vergibt pro Kategorie die Zirkel-Nummern alphabetisch (Nachname) an die
gemeldeten Namen und schreibt pro Zeitslot:

  zuweisung/<slot>-anmeldeliste.csv   Name → Nummer (Sign-on-Tisch; alphabetisch)
  zuweisung/<slot>-import.csv         tag_id;bib;name für den App-Import
                                      (nur mit --master, denn tag_id kommt aus
                                      der tagesmaster.csv der Klebe-Session)

Ohne --master entstehen nur die Anmeldelisten — die Zuordnung Name↔Nummer
steht damit heute schon fest; die Import-Dateien werden nach dem Kleben mit
  python3 nummern_zuweisung.py --master ~/Desktop/tagesmaster.csv
nachgeneriert. Idempotent/deterministisch: gleiche Meldeliste ⇒ gleiche Nummern.
"""
import argparse
import csv
from pathlib import Path

HERE = Path(__file__).parent

# Kategorie → Nummernzirkel (aus "Nummernzirkel Karli Krit.xlsx" + Erik-Ansage
# für den Lauf). None = kein Zirkel bekannt → Kategorie wird nur geflaggt.
CIRCLES = {
    "u15m": range(81, 93),
    "u15w": None,               # kein Zirkel im Excel
    "u17w": range(301, 307),
    "u17m": range(181, 194),   # Erik 07.08.: 191-193 dazu
    "masters_4": range(316, 327),
    "masters_2": range(281, 295),
    "masters_3": range(331, 344),
    "junioren": range(401, 411),  # Erik 07.08.
    "jedermann_leicht": range(1, 76),
    "frauen_elite": range(351, 370),
    "jedefrau": range(371, 382),
    "juniorinnen": range(391, 400),
    "jedermann_mittel": range(101, 176),
    "jedermann_schwer": range(201, 276),
    # Eigene Nummern (Vorschlag aus den tagesweit freien Bereichen 411-500;
    # FG behält die Plakette über Quali + Finals):
    "fixed_gear_men": range(411, 441),
    "flinta": range(441, 451),
    # Lauf: Blöcke lt. Erik (m+w teilen sich den Block)
    "lauf_m_10km": range(101, 200), "lauf_w_10km": range(101, 200),
    "lauf_m_5km": range(301, 400), "lauf_w_5km": range(301, 400),
    "lauf_m_u18_5km": range(501, 600), "lauf_w_u18_5km": range(501, 600),
    "lauf_m_u18_10km": range(501, 600), "lauf_w_u18_10km": range(501, 600),
}


def lastname_key(name: str) -> str:
    parts = name.split()
    return (parts[-1] + " " + " ".join(parts[:-1])).lower() if parts else "~"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", help="tagesmaster.csv (tag_id;bib;…) für Import-Dateien")
    args = ap.parse_args()

    tag_by_bib: dict[str, str] = {}
    if args.master:
        with open(args.master, encoding="utf-8-sig") as f:
            for row in csv.reader(f, delimiter=";"):
                if len(row) >= 2 and row[0] and row[1] and row[0] != "tag_id":
                    tag_by_bib[row[1].strip()] = row[0].strip()
        print(f"Master: {len(tag_by_bib)} Tag↔Nummer-Paare")

    outdir = HERE / "zuweisung"
    outdir.mkdir(exist_ok=True)

    for src in sorted((HERE / "rennen").glob("*.csv")):
        # Meldungen je Kategorie einlesen (Format tag_id;bib;name;kategorie;team)
        by_cat: dict[str, list[dict]] = {}
        with open(src, encoding="utf-8-sig") as f:
            reader = csv.reader(f, delimiter=";")
            next(reader, None)
            for row in reader:
                if len(row) < 4 or not row[2].strip():
                    continue
                by_cat.setdefault(row[3].strip(), []).append(
                    {"name": row[2].strip(), "team": row[4].strip() if len(row) > 4 else ""})

        assigned: list[dict] = []
        # Lauf: m+w teilen sich einen Block → gemeinsam nummerieren.
        # Kategorien, die denselben Zirkel nutzen, zusammenfassen:
        merged: dict[tuple, list] = {}
        flagged: list[str] = []
        for cat, entries in sorted(by_cat.items()):
            circle = CIRCLES.get(cat)
            if circle is None:
                flagged.append(f"{cat} ({len(entries)} Meldungen) — KEIN ZIRKEL")
                continue
            merged.setdefault((circle.start, circle.stop), []).append((cat, entries))

        for (start, stop), groups in merged.items():
            pool = list(range(start, stop))
            everyone = [(cat, e) for cat, entries in groups for e in entries]
            everyone.sort(key=lambda ce: lastname_key(ce[1]["name"]))
            if len(everyone) > len(pool):
                flagged.append(
                    f"Zirkel {start}-{stop - 1}: {len(everyone)} Meldungen für "
                    f"{len(pool)} Nummern — Überlauf bleibt unnummeriert!")
            for (cat, e), num in zip(everyone, pool):
                assigned.append({"bib": num, "name": e["name"], "kategorie": cat,
                                 "team": e["team"]})
            for cat, e in everyone[len(pool):]:
                assigned.append({"bib": "", "name": e["name"], "kategorie": cat,
                                 "team": e["team"]})

        slot = src.stem
        # Anmeldeliste: alphabetisch, fürs schnelle Finden am Tisch
        with open(outdir / f"{slot}-anmeldeliste.csv", "w",
                  encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";", lineterminator="\n")
            w.writerow(["name", "nummer", "kategorie", "team", "abgeholt ☐"])
            for e in sorted(assigned, key=lambda e: lastname_key(e["name"])):
                w.writerow([e["name"], e["bib"], e["kategorie"], e["team"], ""])

        # Import-Datei: nur wenn Master da ist (tag_id-Auflösung über die Nummer)
        note = ""
        if tag_by_bib:
            missing = 0
            with open(outdir / f"{slot}-import.csv", "w",
                      encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=";", lineterminator="\n")
                w.writerow(["tag_id", "bib", "name", "kategorie (Import ignoriert)"])
                for e in sorted([a for a in assigned if a["bib"] != ""],
                                key=lambda e: e["bib"]):
                    tag = tag_by_bib.get(str(e["bib"]), "")
                    if not tag:
                        missing += 1
                    w.writerow([tag, e["bib"], e["name"], e["kategorie"]])
            note = f", Import-Datei geschrieben ({missing} Nummern ohne Tag im Master)" \
                if missing else ", Import-Datei geschrieben"

        print(f"{slot}: {sum(1 for e in assigned if e['bib'] != '')} Nummern vergeben{note}")
        for fl in flagged:
            print(f"   ⚠ {fl}")


if __name__ == "__main__":
    main()
