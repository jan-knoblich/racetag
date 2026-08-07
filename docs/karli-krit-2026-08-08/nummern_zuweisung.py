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
    # 07.08. spät gekürzt (Tag-Budget): Zirkel ~= Meldungen + realistische
    # Nachmelde-Reserve statt voller Excel-Breite.
    "u15m": range(81, 91),        # 6 gemeldet, war 81-92
    "u15w": None,                 # kein Zirkel im Excel
    "u17w": range(301, 305),      # 1 gemeldet, war 301-306
    "u17m": range(181, 194),      # Erik 07.08.: 191-193 dazu — VOLL (13/13)
    "masters_4": range(316, 327),  # VOLL (11/11)
    "masters_2": range(281, 295),
    "masters_3": range(331, 344),
    "junioren": range(401, 411),  # Erik 07.08.
    "jedermann_leicht": range(1, 51),    # 33 gemeldet; gekürzt von 1-75
    "frauen_elite": range(351, 367),     # 13 gemeldet, war -369
    "jedefrau": range(371, 382),
    "juniorinnen": range(391, 396),      # 2 gemeldet, war -399
    "jedermann_mittel": range(101, 176),  # volle Breite (50 gemeldet, Limit 75)
    "jedermann_schwer": range(201, 241),  # 18 gemeldet; gekürzt von -275
    # Eigene Nummern (aus den tagesweit freien Bereichen; FG behält die
    # Plakette über Quali + Finals):
    "fixed_gear_men": range(411, 437),   # 22 gemeldet, war -440
    "flinta": range(441, 447),           # 4 gemeldet, war -450
    # Lauf: Blöcke lt. Erik (m+w teilen sich den Block). Verbreitert über die
    # reinen 100er hinaus, damit +20 % Nachmelde-Puffer reinpassen — die
    # Blöcke bleiben untereinander disjunkt, Radnummern dürfen überlappen.
    "lauf_m_10km": range(101, 221), "lauf_w_10km": range(101, 221),
    "lauf_m_5km": range(301, 421), "lauf_w_5km": range(301, 421),
    "lauf_m_u18_5km": range(501, 531), "lauf_w_u18_5km": range(501, 531),
    "lauf_m_u18_10km": range(501, 531), "lauf_w_u18_10km": range(501, 531),
}

LAUF_PUFFER = 0.20  # Nachmelde-Reserve pro Lauf-Block (aufgerundet)


def lastname_key(name: str) -> str:
    parts = name.split()
    return (parts[-1] + " " + " ".join(parts[:-1])).lower() if parts else "~"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", help="Rad-Master (Export tags des Arbeits-Rennens)")
    ap.add_argument("--master-lauf", help="Lauf-Master (Export tags des Lauf-Rennens) "
                    "— 111 Nummern existieren doppelt (Lauf-Papier + Rad-Plakette), "
                    "jede Gruppe hat ihre eigenen Tags!")
    args = ap.parse_args()

    def load_master(path):
        m: dict[str, str] = {}
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.reader(f, delimiter=";"):
                if len(row) >= 2 and row[0] and row[1] and row[0] != "tag_id":
                    m[row[1].strip()] = row[0].strip()
        return m

    tag_by_bib = load_master(args.master) if args.master else {}
    tag_by_bib_lauf = load_master(args.master_lauf) if args.master_lauf else {}
    if tag_by_bib:
        print(f"Rad-Master: {len(tag_by_bib)} Tag↔Nummer-Paare")
    if tag_by_bib_lauf:
        print(f"Lauf-Master: {len(tag_by_bib_lauf)} Tag↔Nummer-Paare")

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
            # Nachmelde-Puffer als leere Einträge (Name kommt am Sign-on-Tisch;
            # Blatt wird mitgedruckt, Tag mitgekoppelt):
            # - Lauf-Blöcke: +20 % der Meldungen (Blöcke dafür verbreitert)
            # - Rad-Zirkel: der KOMPLETTE Rest des Zirkels — Eriks Zirkel
            #   enthalten den Puffer schon (Jedermann-Limit 75 = Zirkelbreite),
            #   also braucht jede Zirkel-Nummer ein physisches Blatt.
            if any(cat.startswith("lauf_") for cat, _ in groups):
                import math
                n_buf = math.ceil(len(everyone) * LAUF_PUFFER)
                rest = pool[len(everyone):len(everyone) + n_buf]
                if len(rest) < n_buf:
                    flagged.append(
                        f"Zirkel {start}-{stop - 1}: Puffer abgeschnitten "
                        f"({len(rest)} von {n_buf})")
            else:
                rest = pool[len(everyone):]
            for num in rest:
                assigned.append({"bib": num, "name": "",
                                 "kategorie": "nachmelde-puffer", "team": ""})

        slot = src.stem
        # Anmeldeliste: alphabetisch, fürs schnelle Finden am Tisch
        with open(outdir / f"{slot}-anmeldeliste.csv", "w",
                  encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";", lineterminator="\n")
            w.writerow(["name", "nummer", "kategorie", "team", "abgeholt ☐"])
            for e in sorted(assigned, key=lambda e: lastname_key(e["name"])):
                w.writerow([e["name"], e["bib"], e["kategorie"], e["team"], ""])

        # Import-Datei: nur wenn der passende Master da ist. Der Lauf-Slot
        # nutzt den Lauf-Master (eigene Papiernummern + Tags), alle Rad-Slots
        # den Rad-Master.
        is_lauf = slot.startswith("0900")
        master = tag_by_bib_lauf if is_lauf else tag_by_bib
        note = "" if master else " (kein passender Master — keine Import-Datei)"
        if master:
            missing = 0
            with open(outdir / f"{slot}-import.csv", "w",
                      encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f, delimiter=";", lineterminator="\n")
                w.writerow(["tag_id", "bib", "name", "kategorie (Import ignoriert)"])
                for e in sorted([a for a in assigned if a["bib"] != ""],
                                key=lambda e: e["bib"]):
                    tag = master.get(str(e["bib"]), "")
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
