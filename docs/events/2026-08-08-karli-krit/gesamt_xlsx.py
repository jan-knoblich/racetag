#!/usr/bin/env python3
"""Ein editierbares Gesamt-Excel aller Wertungen (Kerstins Vorlagen-Layout).

Für die Weitergabe an die Kampfrichterin: alle Wertungen des Tages in EINER
xlsx-Arbeitsmappe, pro Wertung ein Blatt im Layout der SRB-Vorlage (Kopfblock
mit Veranstaltung, Ort/Datum, Renndistanz), frei änderbar in Excel/Numbers.

Quellen: srb-*.xlsx + fixed-ergebnisse-srb.xlsx (werden 1:1 kopiert) und
ergebnis-lauf-*.csv (bekommen den Vorlagen-Kopf; Verein aus der racetag-DB).

Usage: e2e-venv/bin/python gesamt_xlsx.py  ->  ~/Downloads/karli-ergebnisse-gesamt.xlsx
"""
import csv
import sqlite3
from pathlib import Path

from openpyxl import Workbook, load_workbook

HERE = Path(__file__).parent
OUT = Path.home() / "Downloads/karli-ergebnisse-gesamt.xlsx"

LAUF = [  # (Datei, Blattname, Klasse, Distanz)
    ("ergebnis-lauf-10km-erwachsene.csv", "Lauf 10km", "Lauf 10 km Erwachsene", "13 Runden = 10 km"),
    ("ergebnis-lauf-10km-u18-maennlich.csv", "Lauf 10km U18m", "Lauf 10 km U18 männlich", "13 Runden = 10 km"),
    ("ergebnis-lauf-10km-u18-weiblich.csv", "Lauf 10km U18w", "Lauf 10 km U18 weiblich", "13 Runden = 10 km"),
    ("ergebnis-lauf-5km-erwachsene.csv", "Lauf 5km", "Lauf 5 km Erwachsene", "7 Runden = 5 km"),
    ("ergebnis-lauf-5km-u18-maennlich.csv", "Lauf 5km U18m", "Lauf 5 km U18 männlich", "7 Runden = 5 km"),
    ("ergebnis-lauf-5km-u18-weiblich.csv", "Lauf 5km U18w", "Lauf 5 km U18 weiblich", "7 Runden = 5 km"),
]
RAD = [  # (Datei, [(Quell-Sheet, Blattname)])
    ("srb-11-20-u15-u17w-20-rd.xlsx", [("u15m", "U15"), ("u17w", "U17 weiblich")]),
    ("srb-12-10-u17m-masters4-32-rd.xlsx", [("u17m", "U17 männlich"), ("masters_4", "Masters 4")]),
    ("srb-13-00-masters2-3-junioren-45-rd.xlsx",
     [("masters_2", "Masters 2"), ("masters_3", "Masters 3"), ("junioren", "Junioren U19")]),
    ("srb-15-00-jedermann-leicht-45-min.xlsx", [("jedermann_leicht", "Jedermann leicht")]),
    ("srb-16-00-fixed-gear-quali-5-rd.xlsx", [("fixed_gear_men", "Fixed Quali")]),
    ("srb-16-15-frauen-elite-u19w-jedefrau-30-rd.xlsx",
     [("frauen_elite", "Frauen Elite"), ("juniorinnen", "Juniorinnen U19"), ("jedefrau", "Jederfrau")]),
    ("srb-17-15-jedermann-mittel-45-min.xlsx", [("jedermann_mittel", "Jedermann mittel")]),
    ("srb-18-15-jedermann-schwer-60-min.xlsx", [("jedermann_schwer", "Jedermann schwer")]),
    ("fixed-ergebnisse-srb.xlsx", [("Fixed Gear B", "Fixed B"), ("FLINTA", "FLINTA"), ("Fixed Gear A", "Fixed A")]),
]
BREITEN = {"A": 8, "B": 8, "C": 26, "D": 30, "E": 14, "F": 9, "G": 9}


def lauf_vereine() -> dict[str, str]:
    con = sqlite3.connect(f"file:{Path.home()}/.racetag/data/racetag.db?mode=ro", uri=True)
    race = con.execute("SELECT id FROM races WHERE name LIKE '%Lauf%'").fetchone()
    return {str(b): v for b, v in con.execute(
        "SELECT bib, verein FROM riders WHERE race_id=? AND verein != ''", (race[0],))}


def kopfblock(ws, klasse: str, distanz: str) -> None:
    ws["A1"] = "Sächsischer Radfahrer-Bund e. V."
    ws["A4"] = "Amtliches Ergebnis"
    ws["A6"] = "Karli Krit + Karli Lauf — Revolution Crit"
    ws["F7"] = "Leipzig, 08.08.2026"
    ws["F8"] = "Ort, Datum"
    ws["E10"] = distanz
    ws["A11"] = klasse


def zeit_als_excel(hms: str) -> float | str:
    teile = hms.split(":")
    if len(teile) != 3:
        return hms
    h, m, s = (int(t) for t in teile)
    return (h * 3600 + m * 60 + s) / 86400.0


wb = Workbook()
wb.remove(wb.active)
vereine = lauf_vereine()

for datei, blatt, klasse, distanz in LAUF:
    ws = wb.create_sheet(blatt)
    kopfblock(ws, klasse, distanz)
    for spalte, breite in BREITEN.items():
        ws.column_dimensions[spalte].width = breite
    for j, h in enumerate(["Platz", "St.-Nr.", "Name, Vorname", "Verein",
                           "Zeit", "Runden", "Hinweis"], 1):
        ws.cell(row=14, column=j, value=h)
    out_row = 15
    with open(HERE / datei, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            ws.cell(row=out_row, column=1,
                    value=int(r["platz"]) if r["platz"].isdigit() else r["platz"])
            ws.cell(row=out_row, column=2, value=int(r["nummer"]))
            ws.cell(row=out_row, column=3, value=r["name"])
            ws.cell(row=out_row, column=4, value=vereine.get(r["nummer"], ""))
            if r["zeit"]:
                c = ws.cell(row=out_row, column=5, value=zeit_als_excel(r["zeit"]))
                c.number_format = "h:mm:ss"
            ws.cell(row=out_row, column=6, value=int(r["runden"]))
            ws.cell(row=out_row, column=7, value=r["hinweis"])
            out_row += 1

for datei, sheets in RAD:
    src_wb = load_workbook(HERE / datei)
    for quelle, blatt in sheets:
        src = src_wb[quelle]
        ws = wb.create_sheet(blatt)
        for spalte, breite in BREITEN.items():
            ws.column_dimensions[spalte].width = breite
        for row in src.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                ziel = ws.cell(row=cell.row, column=cell.column, value=cell.value)
                if cell.number_format != "General":
                    ziel.number_format = cell.number_format

wb.save(OUT)
print(f"{OUT}  ({len(wb.sheetnames)} Blätter)")
