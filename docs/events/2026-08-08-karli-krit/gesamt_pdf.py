#!/usr/bin/env python3
"""Gesamt-Ergebnis-PDF: alle Wertungen des Tages in einem Dokument.

Quellen: die srb-*.xlsx (Rad, inkl. bestätigter Korrekturen), die
fixed-ergebnisse-srb.xlsx (Fixed-Gear-Finals) und die ergebnis-lauf-*.csv
(Lauf, inkl. geretteter Finisher). Reihenfolge = Tagesplan.

Usage: e2e-venv/bin/python gesamt_pdf.py  ->  ~/Downloads/karli-krit-ergebnisse-gesamt.pdf
"""
import csv
import datetime as dt
from pathlib import Path

from openpyxl import load_workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

HERE = Path(__file__).parent
OUT_LAUF = Path.home() / "Downloads/karli-lauf-ergebnisse.pdf"
OUT_RAD = Path.home() / "Downloads/karli-krit-ergebnisse.pdf"

LABELS = {
    "u15m": "Schüler U15 männlich", "u17w": "Jugend U17 weiblich",
    "u17m": "Jugend U17 männlich", "masters_4": "Masters 4",
    "masters_2": "Masters 2", "masters_3": "Masters 3",
    "junioren": "Junioren U19", "jedermann_leicht": "Jedermann leicht",
    "fixed_gear_men": "Fixed Gear Qualifying",
    "frauen_elite": "Frauen Elite/Lizenz", "juniorinnen": "Juniorinnen U19",
    "jedefrau": "Jedefrau", "jedermann_mittel": "Jedermann mittel",
    "jedermann_schwer": "Jedermann schwer",
    "Fixed Gear B": "Fixed Gear B-Finale", "FLINTA": "FLINTA*",
    "Fixed Gear A": "Fixed Gear A-Finale",
}
# (Überschrift Slot, Datei, Sheets in Reihenfolge)
XLSX = [
    ("11:20 Uhr", "srb-11-20-u15-u17w-20-rd.xlsx", ["u15m", "u17w"]),
    ("12:10 Uhr", "srb-12-10-u17m-masters4-32-rd.xlsx", ["u17m", "masters_4"]),
    ("13:00 Uhr", "srb-13-00-masters2-3-junioren-45-rd.xlsx",
     ["masters_2", "masters_3", "junioren"]),
    ("15:00 Uhr", "srb-15-00-jedermann-leicht-45-min.xlsx", ["jedermann_leicht"]),
    ("16:00 Uhr", "srb-16-00-fixed-gear-quali-5-rd.xlsx", ["fixed_gear_men"]),
    ("16:15 Uhr", "srb-16-15-frauen-elite-u19w-jedefrau-30-rd.xlsx",
     ["frauen_elite", "juniorinnen", "jedefrau"]),
    ("17:15 Uhr", "srb-17-15-jedermann-mittel-45-min.xlsx", ["jedermann_mittel"]),
    ("18:15 Uhr", "srb-18-15-jedermann-schwer-60-min.xlsx", ["jedermann_schwer"]),
    ("19:30 / 20:00 Uhr", "fixed-ergebnisse-srb.xlsx",
     ["Fixed Gear B", "FLINTA", "Fixed Gear A"]),
]

h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=17, leading=21,
                    spaceAfter=2)
sub = ParagraphStyle("sub", fontName="Helvetica", fontSize=10.5, leading=13,
                     textColor=colors.HexColor("#555555"), spaceAfter=10)
h2 = ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=13, leading=16,
                    spaceBefore=10, spaceAfter=4,
                    textColor=colors.HexColor("#b03090"))
note = ParagraphStyle("note", fontName="Helvetica", fontSize=8, leading=10,
                      textColor=colors.HexColor("#666666"), spaceBefore=2)
cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=8.5, leading=10.5)


def fmt_zeit(v):
    if isinstance(v, dt.time):
        return f"{v.hour}:{v.minute:02d}:{v.second:02d}"
    return str(v) if v is not None else ""


def tabelle(header, rows, widths):
    data = [[Paragraph(f"<b>{h}</b>", cell) for h in header]]
    for r in rows:
        data.append([Paragraph(str(c) if c is not None else "", cell) for c in r])
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#aaaaaa")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2d9ec")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))
    return t


STAND = ("Leipzig, 08.08.2026 · Zeitnahme racetag (RFID) · Stand 11.08.2026 · "
         "Runden/Zeiten: racetag inkl. aller vom Wettkampfgericht bestätigten "
         "Korrekturen · Punkte und Überrundungen: Amtliche Ergebnislisten")
story_lauf: list = [
    Paragraph("Karli Lauf — Ergebnisse", h1),
    Paragraph(STAND + " · Start 09:00 Uhr", sub),
]
story_rad: list = [
    Paragraph("Karli Krit — Ergebnisse (Rad)", h1),
    Paragraph(STAND, sub),
]

# ---- Lauf (09:00) ----
story = story_lauf
for datei, titel in [
        ("ergebnis-lauf-10km-erwachsene.csv", "10 km Erwachsene"),
        ("ergebnis-lauf-5km-erwachsene.csv", "5 km Erwachsene"),
        ("ergebnis-lauf-10km-u18-maennlich.csv", "10 km U18 männlich"),
        ("ergebnis-lauf-10km-u18-weiblich.csv", "10 km U18 weiblich"),
        ("ergebnis-lauf-5km-u18-maennlich.csv", "5 km U18 männlich"),
        ("ergebnis-lauf-5km-u18-weiblich.csv", "5 km U18 weiblich")]:
    rows_ok, rows_ng, korrigiert, vermerke = [], [], 0, []
    with open(HERE / datei, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["platz"].strip() == "-":
                rows_ng.append(r)
            else:
                hinweis = r["hinweis"].strip()
                stern = "*" if hinweis else ""
                if "KORRIGIERT" in hinweis or "Startüberfahrt" in hinweis:
                    korrigiert += 1
                elif hinweis:
                    vermerke.append(
                        f"* Nr. {r['nummer']} {r['name']}: "
                        + hinweis.replace("GEWERTET MIT VERMERK: ", ""))
                rows_ok.append([r["platz"], r["nummer"], r["name"],
                                r["zeit"] + stern])
    story.append(Paragraph(f"Lauf — {titel}", h2))
    if rows_ok:
        story.append(tabelle(["Platz", "St.-Nr.", "Name", "Zeit"], rows_ok,
                             [1.6 * cm, 1.8 * cm, 9.6 * cm, 2.6 * cm]))
    if korrigiert:
        story.append(Paragraph(
            f"* {korrigiert}x Wertung korrigiert: Lesung(en) unterwegs nachweislich "
            "verpasst bzw. Startüberfahrt ergänzt — Ziel = letzte Überfahrt "
            "(Details in den ergebnis-CSVs)", note))
    for v in vermerke:
        story.append(Paragraph(v, note))
    if rows_ng:
        def kurz(r):
            if "nur die letzte fehlt" in r["hinweis"]:
                return (f"Nr. {r['nummer']} {r['name']} ({r['ueberfahrten']} Überf., "
                        "Ziellesung fehlt — nicht wertbar)")
            return f"Nr. {r['nummer']} {r['name']} ({r['ueberfahrten']} Überf.)"
        story.append(Paragraph(
            "Nicht gewertet: " + ", ".join(kurz(r) for r in rows_ng), note))

# ---- Rad ----
story = story_rad
for slot, datei, sheets in XLSX:
    wb = load_workbook(HERE / datei)
    for sheet in sheets:
        ws = wb[sheet]
        header = [c.value for c in ws[14] if c.value]
        lizenz = "UCI-ID" in header
        hat_punkte = "Punkte" in header
        rows, hinweise = [], []
        for row in ws.iter_rows(min_row=15, values_only=True):
            if row[0] is None and row[1] is None:
                continue
            if isinstance(row[0], str) and row[0].startswith("Hinweis:"):
                hinweise.append(row[0])
                continue
            runden = row[6] if (lizenz and hat_punkte) else row[5]
            if not runden:
                continue  # 0 Runden = nie gestartet (Reserve-Plakette/DNS)
            if lizenz and hat_punkte:
                rows.append([row[0], row[1], row[2], row[3] or "", row[4] or "",
                             "" if row[5] is None else row[5], row[6]])
            elif lizenz:
                rows.append([row[0], row[1], row[2], row[3] or "", row[4] or "",
                             row[5]])
            else:
                rows.append([row[0], row[1], row[2], row[3] or "",
                             fmt_zeit(row[4]), row[5]])
        story.append(Paragraph(f"{slot} — {LABELS.get(sheet, sheet)}", h2))
        if lizenz and hat_punkte:
            story.append(tabelle(
                ["Platz", "St.-Nr.", "Name", "Verein", "UCI-ID", "Punkte", "Runden"],
                rows, [1.3 * cm, 1.5 * cm, 4.6 * cm, 4.4 * cm, 2.5 * cm,
                       1.4 * cm, 1.5 * cm]))
        elif lizenz:
            story.append(tabelle(
                ["Platz", "St.-Nr.", "Name", "Verein", "UCI-ID", "Runden"],
                rows, [1.3 * cm, 1.5 * cm, 5.0 * cm, 5.0 * cm, 2.5 * cm, 1.5 * cm]))
        else:
            story.append(tabelle(
                ["Platz", "St.-Nr.", "Name", "Verein", "Zeit", "Runden"],
                rows, [1.3 * cm, 1.5 * cm, 5.4 * cm, 4.6 * cm, 2.2 * cm, 1.5 * cm]))
        for h in hinweise:
            story.append(Paragraph(h, note))

for out, titel, st in [(OUT_LAUF, "Karli Lauf 2026 — Ergebnisse", story_lauf),
                       (OUT_RAD, "Karli Krit 2026 — Ergebnisse (Rad)", story_rad)]:
    doc = SimpleDocTemplate(str(out), pagesize=A4,
                            topMargin=1.4 * cm, bottomMargin=1.4 * cm,
                            leftMargin=1.6 * cm, rightMargin=1.6 * cm,
                            title=titel)
    doc.build(st)
    print(out)
