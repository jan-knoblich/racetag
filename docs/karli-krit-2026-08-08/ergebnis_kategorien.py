#!/usr/bin/env python3
"""Ergebnisliste in Kategorie-Wertungen aufsplitten (Aushang pro Klasse).

Nimmt ein Rennen-Ergebnis — racetag „Export results"-CSV ODER eine
ergebnis-lauf-*.csv aus lauf_auswertung.py — und schreibt pro Kategorie eine
eigene Datei mit neu durchnummerierten Plätzen. Die Kategorie kommt aus den
zuweisung/*-anmeldeliste.csv (Nummer→Kategorie des passenden Slots); Nummern
ohne bekannte Kategorie (spontane Nachmelde-Puffer) landen gesammelt in
"unbekannt" zum manuellen Einsortieren.

Der Slot wird aus der '# Race:'-Kopfzeile bzw. dem Dateinamen erkannt;
sonst --slot HHMM angeben.

Usage: python3 ergebnis_kategorien.py ergebnis.csv [--slot 1300]
"""
import argparse
import csv
import io
import re
import sys
from pathlib import Path

HERE = Path(__file__).parent

SLOT_HINTS = [
    ("lauf", "0900"), ("kids", "1030"), ("u15", "1120"), ("u17m", "1210"),
    ("masters2", "1300"), ("leicht", "1500"), ("quali", "1600"),
    ("frauen", "1615"), ("mittel", "1715"), ("schwer", "1815"),
    ("flinta", "1930"), ("fixed gear b", "1930"), ("a final", "2000"),
]


def detect_slot(path, comments):
    text = " ".join(comments).lower() + " " + Path(path).name.lower()
    for hint, slot in SLOT_HINTS:
        if hint in text:
            return slot
    return None


def load_kategorien(slot):
    src = next((HERE / "zuweisung").glob(f"{slot}-*anmeldeliste.csv"), None)
    if src is None:
        sys.exit(f"Keine Anmeldeliste für Slot {slot} in zuweisung/ gefunden.")
    kat = {}
    with open(src, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            n = r.get("nummer", "").strip()
            k = r.get("kategorie", "").strip()
            if n and k and k not in ("nachmelde-puffer",):
                kat[n] = k
    return kat


def read_ergebnis(path):
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    comments = [l for l in raw.splitlines() if l.startswith("#")]
    body = "\n".join(l for l in raw.splitlines() if l and not l.startswith("#"))
    first = body.splitlines()[0]
    delim = ";" if first.count(";") > first.count(",") else ","
    rows = list(csv.reader(io.StringIO(body), delimiter=delim))
    return comments, rows[0], rows[1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ergebnis")
    ap.add_argument("--slot", default=None)
    args = ap.parse_args()

    comments, header, rows = read_ergebnis(args.ergebnis)
    slot = args.slot or detect_slot(args.ergebnis, comments)
    if not slot:
        sys.exit("Slot nicht erkennbar — bitte --slot HHMM angeben.")
    kat = load_kategorien(slot)
    print(f"Slot {slot}: {len(kat)} Nummern mit Kategorie")

    lower = [h.strip().lower() for h in header]
    bib_i = lower.index("bib") if "bib" in lower else lower.index("nummer")
    pos_i = lower.index("position") if "position" in lower else (
        lower.index("platz") if "platz" in lower else None)

    by_kat = {}
    for r in rows:
        bib = r[bib_i].strip()
        k = kat.get(bib, "unbekannt")
        by_kat.setdefault(k, []).append(r)

    stem = re.sub(r"\.csv$", "", args.ergebnis)
    for k, krows in sorted(by_kat.items()):
        out = f"{stem}-{k}.csv"
        with open(out, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";", lineterminator="\n")
            for c in comments:
                f.write(c + "\n")
            f.write(f"# Kategorie: {k}\n")
            w.writerow(header)
            platz = 0
            for r in krows:  # Eingabe ist bereits nach Zeit/Platz sortiert
                r = list(r)
                if pos_i is not None:
                    if r[pos_i].strip() and r[pos_i].strip() != "-":
                        platz += 1
                        r[pos_i] = platz  # innerhalb der Kategorie neu zählen
                w.writerow(r)
        print(f"  {k}: {len(krows)} Zeilen -> {Path(out).name}")
    if "unbekannt" in by_kat:
        print("  ⚠ 'unbekannt' = Nummern ohne Kategorie (Nachmelde-Puffer) — "
              "manuell einsortieren!")


if __name__ == "__main__":
    main()
