#!/usr/bin/env python3
"""Ein editierbares Gesamt-Excel aller RAD-Wertungen (Kerstins Vorlagen-Layout).

Für die Weitergabe an die Kampfrichterin: alle Rad-Wertungen des Tages in
EINER xlsx-Arbeitsmappe, pro Wertung ein Blatt im Layout der SRB-Vorlage
(Kopfblock mit Veranstaltung, Ort/Datum, Renndistanz), frei änderbar.
Der Lauf bleibt bewusst draußen (eigenes Format: CSVs + Lauf-PDF).

Quellen: srb-*.xlsx + fixed-ergebnisse-srb.xlsx (werden 1:1 kopiert).

Usage: e2e-venv/bin/python gesamt_xlsx.py  ->  ~/Downloads/karli-ergebnisse-gesamt.xlsx
"""
from pathlib import Path

from openpyxl import Workbook, load_workbook

from srb_stil import formatiere

HERE = Path(__file__).parent
OUT = Path.home() / "Downloads/karli-ergebnisse-gesamt.xlsx"

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


wb = Workbook()
wb.remove(wb.active)

for datei, sheets in RAD:
    src_wb = load_workbook(HERE / datei)
    for quelle, blatt in sheets:
        src = src_wb[quelle]
        ws = wb.create_sheet(blatt)
        for row in src.iter_rows():
            for cell in row:
                if cell.value is None:
                    continue
                ziel = ws.cell(row=cell.row, column=cell.column, value=cell.value)
                if cell.number_format != "General":
                    ziel.number_format = cell.number_format
        spalten = sum(1 for c in src[14] if c.value)
        formatiere(ws, spalten or 6)

wb.save(OUT)
print(f"{OUT}  ({len(wb.sheetnames)} Blätter)")
