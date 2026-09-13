#!/usr/bin/env python3
"""Ergebnisse mit Eriks Startliste zusammenführen — der Nachhinein-Weg.

Tagsüber läuft die Zeitmessung rein über Nummern; Namen müssen NIE live
eingepflegt werden. Nach dem Rennen: „Export results" (oder für den Lauf die
ergebnis-lauf-*.csv aus lauf_auswertung.py) + Eriks Liste (Nummer↔Name) →
dieses Skript schreibt die fertige Ergebnisliste mit Namen.

Funktioniert mit beiden Ergebnis-Formaten:
  - racetag-Export: '#'-Kopfzeilen, Komma-getrennt, Spalten bib/name/…
  - lauf_auswertung: Semikolon, Spalten nummer/name/…

Eriks Liste: zwei Spalten pro Zeile (Nummer und Name, Reihenfolge egal,
Trennzeichen ; , oder Tab, Kopfzeile erlaubt).

Usage: python3 ergebnis_merge.py ergebnis.csv erik-liste.csv [-o fertig.csv]
Default-Ausgabe: <ergebnis>-mit-namen.csv (Semikolon + BOM, Excel-tauglich).
"""
import argparse
import csv
import io
import re
import sys
from pathlib import Path


def parse_liste(path):
    pairs = {}
    skipped = []
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip().lstrip("﻿")
            if not line:
                continue
            cells = [c.strip() for c in re.split(r"[;\t,]", line) if c.strip()]
            nums = [c for c in cells if c.isdigit()]
            texts = [c for c in cells if not c.isdigit()]
            if len(nums) == 1 and texts:
                pairs[nums[0]] = " ".join(texts)
            elif lineno > 1:
                skipped.append(f"Zeile {lineno}: {line!r}")
    return pairs, skipped


def read_ergebnis(path):
    """(kommentarzeilen, header, rows) — Delimiter wird erkannt."""
    raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    comments = [l for l in raw.splitlines() if l.startswith("#")]
    body = "\n".join(l for l in raw.splitlines() if l and not l.startswith("#"))
    delim = ";" if body.splitlines()[0].count(";") > body.splitlines()[0].count(",") else ","
    rows = list(csv.reader(io.StringIO(body), delimiter=delim))
    return comments, rows[0], rows[1:]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ergebnis")
    ap.add_argument("liste")
    ap.add_argument("-o", "--out", default=None)
    args = ap.parse_args()

    names, skipped = parse_liste(args.liste)
    print(f"Eriks Liste: {len(names)} Nummern")
    for s in skipped:
        print(f"  ⚠ {s}")

    comments, header, rows = read_ergebnis(args.ergebnis)
    lower = [h.strip().lower() for h in header]
    try:
        bib_i = lower.index("bib") if "bib" in lower else lower.index("nummer")
    except ValueError:
        sys.exit(f"Keine bib/nummer-Spalte im Ergebnis gefunden: {header}")
    if "name" in lower:
        name_i = lower.index("name")
    else:
        name_i = len(header)
        header = list(header) + ["name"]
        rows = [list(r) + [""] for r in rows]

    filled = kept = unknown = 0
    seen_bibs = set()
    for r in rows:
        bib = r[bib_i].strip()
        seen_bibs.add(bib)
        if bib in names:
            if r[name_i].strip() and r[name_i].strip() != names[bib]:
                print(f"  Nr. {bib}: '{r[name_i].strip()}' -> '{names[bib]}' (Erik gewinnt)")
            r[name_i] = names[bib]
            filled += 1
        elif r[name_i].strip():
            kept += 1
        elif bib:
            print(f"  ⚠ Nr. {bib} hat WEDER Namen im Export noch in Eriks Liste")
            unknown += 1

    no_shows = sorted((b for b in names if b not in seen_bibs), key=int)

    out = args.out or re.sub(r"\.csv$", "", args.ergebnis) + "-mit-namen.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        for c in comments:
            f.write(c + "\n")
        w = csv.writer(f, delimiter=";", lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)

    print(f"\n{filled} Namen aus Eriks Liste, {kept} aus racetag übernommen, "
          f"{unknown} ohne Namen.")
    if no_shows:
        print(f"Nicht im Ergebnis (No-Show/DNS?): {', '.join(no_shows)}")
    print(f"-> {out}")


if __name__ == "__main__":
    main()
