#!/usr/bin/env python3
"""Ergebnis-PDFs (Lauf + Rad) im kami-Design (~/.claude/skills/kami).

Quellen: die srb-*.xlsx (Rad, inkl. bestätigter Korrekturen), die
fixed-ergebnisse-srb.xlsx (Fixed-Gear-Finals) und die ergebnis-lauf-*.csv
(Lauf, inkl. geretteter Finisher). Reihenfolge = Tagesplan.

Rendering: WeasyPrint + long-doc-en-Template des kami-Skills — dessen CSS
wird zur Laufzeit unverändert übernommen (nur Fontpfade absolutiert +
Ergebnislisten-Addendum: lange Tabellen dürfen über Seiten laufen).
Start ggf. mit DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib (pango).

Usage: e2e-venv/bin/python gesamt_pdf.py
   ->  ~/Downloads/karli-lauf-ergebnisse.pdf + karli-krit-ergebnisse.pdf
"""
import csv
import datetime as dt
import html
import re
from pathlib import Path

from openpyxl import load_workbook

HERE = Path(__file__).parent
KAMI = Path.home() / ".claude/skills/kami"
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

STAND = ("Zeitnahme racetag (RFID) · Stand 11.08.2026 · Runden/Zeiten: racetag "
         "inkl. aller vom Wettkampfgericht bestätigten Korrekturen · Punkte und "
         "Überrundungen: Amtliche Ergebnislisten")

# Ergebnislisten-Addendum zum unveränderten kami-Template-CSS.
ADDENDUM = """
/* Addendum Ergebnislisten: lange Tabellen laufen über Seiten (thead
   wiederholt WeasyPrint automatisch), Zeilen bleiben ganz. */
table { break-inside: auto; }
tr { break-inside: avoid; }
td, th { font-variant-numeric: tabular-nums; }
h2 { break-after: avoid; }
.dok-kopf { margin-bottom: 22pt; }
.fussnote { font-family: var(--sans); font-size: 8.5pt; color: var(--stone);
            line-height: 1.5; margin: -6pt 0 12pt 0; }
"""


def kami_css(doc_title: str) -> str:
    tpl = (KAMI / "assets/templates/long-doc-en.html").read_text()
    css = re.search(r"<style>(.*?)</style>", tpl, re.S).group(1)
    css = css.replace("../fonts/", (KAMI / "assets/fonts").as_uri() + "/")
    css = css.replace("{{DOC_TITLE}}", doc_title)
    return css + ADDENDUM


def esc(v) -> str:
    return html.escape(str(v)) if v is not None else ""


def fmt_zeit(v):
    if isinstance(v, dt.time):
        return f"{v.hour}:{v.minute:02d}:{v.second:02d}"
    return str(v) if v is not None else ""


def tabelle(header: list[str], rows: list[list]) -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in header)
    trs = "".join(
        "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>"


def fussnote(text: str) -> str:
    return f'<p class="fussnote">{esc(text)}</p>'


def kopf(titel: str, meta: str) -> str:
    return (f'<div class="dok-kopf">'
            f'<div class="cover-eyebrow">Amtliches Ergebnis</div>'
            f'<h1>{esc(titel)}</h1>'
            f'<p class="cover-meta">{esc(meta)}</p></div>')


# ---- Lauf (09:00) ----
lauf_body = [kopf("Karli Lauf 2026",
                  f"Leipzig, 08.08.2026 · Start 09:00 Uhr · {STAND}")]
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
                if "KORRIGIERT" in hinweis or "Startmessung" in hinweis:
                    korrigiert += 1
                elif hinweis:
                    vermerke.append(
                        f"* Nr. {r['nummer']} {r['name']}: "
                        + hinweis.replace("GEWERTET MIT VERMERK: ", ""))
                rows_ok.append([r["platz"], r["nummer"], r["name"],
                                r["zeit"] + stern])
    lauf_body.append(f"<h2>{esc(titel)}</h2>")
    if rows_ok:
        lauf_body.append(tabelle(["Platz", "St.-Nr.", "Name", "Zeit"], rows_ok))
    if korrigiert:
        lauf_body.append(fussnote(
            f"* {korrigiert}x Wertung korrigiert: Lesung(en) unterwegs "
            "nachweislich verpasst bzw. Startmessung ergänzt — Ziel = letzte "
            "Messung (Details in den ergebnis-CSVs)"))
    for v in vermerke:
        lauf_body.append(fussnote(v))
    if rows_ng:
        def kurz(r):
            rd = f"{r['runden']} Runde" + ("" if r["runden"] == "1" else "n")
            if "keine Zielmessung" in r["hinweis"]:
                return (f"Nr. {r['nummer']} {r['name']} ({rd}, "
                        "Zielmessung fehlt — nicht wertbar)")
            return f"Nr. {r['nummer']} {r['name']} ({rd})"
        lauf_body.append(fussnote(
            "Nicht gewertet: " + ", ".join(kurz(r) for r in rows_ng)))

# ---- Rad ----
rad_body = [kopf("Karli Krit 2026", f"Leipzig, 08.08.2026 · {STAND}")]
for slot, datei, sheets in XLSX:
    wb = load_workbook(HERE / datei)
    rad_body.append(f'<div class="chapter-num" style="margin-top:18pt">{esc(slot)}</div>')
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
        rad_body.append(f"<h2>{esc(LABELS.get(sheet, sheet))}</h2>")
        if lizenz and hat_punkte:
            rad_body.append(tabelle(
                ["Platz", "St.-Nr.", "Name", "Verein", "UCI-ID", "Punkte",
                 "Runden"], rows))
        elif lizenz:
            rad_body.append(tabelle(
                ["Platz", "St.-Nr.", "Name", "Verein", "UCI-ID", "Runden"], rows))
        else:
            rad_body.append(tabelle(
                ["Platz", "St.-Nr.", "Name", "Verein", "Zeit", "Runden"], rows))
        for h in hinweise:
            rad_body.append(fussnote(h))

from weasyprint import HTML  # noqa: E402 (Import nach Datenaufbau ok)

for out, doc_title, body in [
        (OUT_LAUF, "Karli Lauf 2026 — Ergebnisse", lauf_body),
        (OUT_RAD, "Karli Krit 2026 — Ergebnisse (Rad)", rad_body)]:
    page = (f'<!DOCTYPE html><html lang="de"><head><meta charset="utf-8">'
            f"<title>{esc(doc_title)}</title><style>{kami_css(doc_title)}</style>"
            f"</head><body>{''.join(body)}</body></html>")
    HTML(string=page).write_pdf(str(out))
    print(out)
