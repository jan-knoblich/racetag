#!/usr/bin/env python3
"""SRB-„Amtliches Ergebnis"-Export direkt aus racetag.

Für jedes angegebene Rennen: Klassement + Fahrer (inkl. Verein/UCI-ID) aus
der laufenden App holen und als formatiertes xlsx im Layout der offiziellen
Ergebnisliste schreiben — pro Kategorie ein Sheet (Kategorie-Zuordnung über
zuweisung/<slot>-anmeldeliste.csv, wenn vorhanden; sonst ein Sheet).

Lizenz-Kategorien:  Platz | St.-Nr. | Name, Vorname | Verein | UCI-ID | Punkte | Runden
Jedermann/Lauf/FG:  Platz | St.-Nr. | Name, Vorname | Verein | Zeit | Runden
(Zeit = total_time als echte Excel-Zeit h:mm:ss; Punkte bleiben leer —
Wertungssprints kennt racetag bewusst nicht.)

Usage: e2e-venv/bin/python srb_export.py "Masters2" ["Frauen" …]
       [--titel "…"] [--ort "Leipzig"] — Ausgabe: srb-<rennen>.xlsx
"""
import argparse
import csv
import io
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

from openpyxl import Workbook

from srb_stil import formatiere

HERE = Path(__file__).parent

LIZENZ = {"u15m", "u15w", "u17w", "u17m", "masters_2", "masters_3", "masters_4",
          "junioren", "juniorinnen", "frauen_elite"}
SLOT_HINTS = [
    ("lauf", "0900"), ("u15", "1120"), ("u17m", "1210"), ("masters2", "1300"),
    ("leicht", "1500"), ("quali", "1600"), ("frauen", "1615"),
    ("mittel", "1715"), ("schwer", "1815"), ("flinta", "1930"),
    ("fixed gear b", "1930"), ("a final", "2000"),
]

# ---- Amtliche Wertung (Abgleich 10.08. mit Kerstins Listen + Foto-Blättern) ----
# Punkte aus Wertungssprints — racetag kennt sie nicht, Quelle: Amtliche
# Ergebnisse. Platzierung in diesen Kategorien: Punkte > Runden > Zieleinlauf.
PUNKTE = {
    "u15m": {"91": 40, "81": 18, "86": 15, "89": 9, "85": 6},
    "masters_2": {"293": 28, "289": 25, "285": 17, "283": 2, "286": 1},
    "masters_3": {"342": 6},
    "masters_4": {"319": 12, "321": 4, "322": 1},
    "junioren": {"401": 22, "405": 13, "407": 7},
    # U17m lt. Amtlichem Ergebnis (Blatt "U17 männl.", nachgereicht 12.08.);
    # 190 vor 192 bei Punktgleichheit: Kampfrichter-Reihung (RANG_FIX unten).
    "u17m": {"303": 40, "187": 27, "190": 7, "192": 7, "182": 3},
}
# Jan 10.08.: die Frauen-Rennen liefen OHNE Wertungssprints — diese Sheets
# bekommen (wie Kerstins 5.1-Blatt) gar keine Punkte-Spalte.
OHNE_PUNKTE = {"frauen_elite", "juniorinnen"}
# Kerstin: 408/410 fahren Masters 3 (nicht Junioren); 92/302/303 fuhren als
# Nachmeldungen das 12:10er-Rennen in der U17m (Tags gelesen, Runden gezählt).
KATEGORIE_FIX = {
    ("1300", "408"): "masters_3", ("1300", "410"): "masters_3",
    ("1210", "92"): "u17m", ("1210", "302"): "u17m", ("1210", "303"): "u17m",
}
# Im 11:20er-Rennen sind 92/302/303 nie gestartet (fuhren 12:10) — raus.
DROP = {("1120", "92"), ("1120", "302"), ("1120", "303")}
# Überrundete: App zählt bis zum End-Klick weiter (84 hat 22 rohe Über-
# fahrten!), amtlich gilt der Rückstand beim Abwinken.
RUNDEN_AMTLICH = {("1120", "84"): 20, ("1120", "90"): 20, ("1120", "87"): 17}
# Zieleinlauf-Korrektur der Kampfrichter: 90 vor 84 (beide −1 Rd., 0 Pkt.).
# U17m (Jan 11.08.): Kampfrichter-Podium übernommen — 190 vor die übrigen
# 31-Runden-Fahrer (1./2. = 303/187 stimmen per Rundenzahl ohnehin).
RANG_FIX = {("1120", "90"): 0, ("1120", "84"): 1, ("1210", "190"): 0}
HINWEISE = {
    "u17m": ["Punkte lt. Amtlichem Ergebnis; bei Punktgleichheit (190/192) "
             "Kampfrichter-Reihung.",
             "Nr. 92 und Nr. 302 (Nachmeldungen) sind im amtlichen Blatt ohne "
             "Platz notiert (\u201evakant\u201c) — hier regulär nach Zieleinlauf "
             "racetag eingereiht."],
    "masters_4": ["Nr. 317: amtlich −3 Rd. notiert; racetag zählt 28 lückenlose "
                  "Überfahrten (vorzeitig beendet, keine Lücke im Signal)."],
    "u15m": ["Runden Nr. 84/90/87 amtlich übernommen (App zählte nach dem "
             "Abwinken weiter)."],
    "frauen_elite": ["Rennen ohne Wertungssprints — keine Punktewertung."],
    "juniorinnen": ["Rennen ohne Wertungssprints — keine Punktewertung."],
}


def req(base, method, path, body=None, raw=False):
    r = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"}, method=method)
    with urllib.request.urlopen(r, timeout=15) as x:
        data = x.read()
        return data.decode("utf-8-sig") if raw else json.loads(data)


def discover_base():
    out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                         capture_output=True, text=True, check=False).stdout
    ports = {l.rsplit(":", 1)[-1].split(" ")[0].replace("(LISTEN)", "").strip()
             for l in out.splitlines() if "127.0.0.1:" in l}
    for port in sorted(ports):
        base = f"http://127.0.0.1:{port}"
        try:
            if "active_race_id" in req(base, "GET", "/races"):
                return base
        except Exception:
            continue
    return None


def load_kategorien(race_name):
    text = race_name.lower()
    slot = next((s for hint, s in SLOT_HINTS if hint in text), None)
    if slot is None:
        return None, {}
    src = next((HERE / "zuweisung").glob(f"{slot}-*anmeldeliste.csv"), None)
    if src is None:
        return slot, {}
    kat = {}
    with open(src, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f, delimiter=";"):
            n, k = r.get("nummer", "").strip(), r.get("kategorie", "").strip()
            if n and k and k != "nachmelde-puffer":
                kat[n] = k
    return slot, kat


# Klassenbezeichnungen nach Kerstins Vorlage (Nummerierung der SRB-Blätter).
KLASSEN = {
    "u15m": "1.1 Schüler U15", "u15w": "1.2 Schülerinnen U15",
    "u17w": "1.3 Jugend U17 weiblich", "masters_4": "2.1 Masters 4",
    "u17m": "2.2 Jugend U17 männlich", "masters_2": "3.1 Masters 2",
    "masters_3": "3.2 Masters 3", "junioren": "3.3 Junioren U19",
    "jedermann_leicht": "4.1 Jedermann leicht",
    "jedermann_mittel": "4.2 Jedermann mittel",
    "jedermann_schwer": "4.3 Jedermann schwer",
    "frauen_elite": "5.1 Frauen Elite (WT, CPT, CT)",
    "juniorinnen": "5.2 Juniorinnen U19", "jedefrau": "6.1 Jederfrau",
    "fixed_gear_men": "Fixed Gear Qualifying",
}


def slugify(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rennen", nargs="+", help="Rennnamen-Fragmente")
    ap.add_argument("--titel", default="Karli Krit- 3. Lauf Revolution Crit")
    ap.add_argument("--ort", default="Leipzig")
    args = ap.parse_args()

    base = discover_base()
    if not base:
        sys.exit("App läuft nicht?")
    races = req(base, "GET", "/races")
    orig = races["active_race_id"]

    for frag in args.rennen:
        race = next((r for r in races["items"]
                     if frag.lower() in r["name"].lower()), None)
        if race is None:
            print(f"⚠ kein Rennen zu {frag!r}")
            continue
        req(base, "POST", f"/races/{race['id']}/activate")
        riders = {r["bib"]: r for r in req(base, "GET", "/riders")["items"]}
        text = req(base, "GET", "/classification.csv", raw=True)
        body = "\n".join(l for l in text.splitlines() if l and not l.startswith("#"))
        rows = list(csv.DictReader(io.StringIO(body)))
        slot, kat = load_kategorien(race["name"])
        slot = slot or ""
        datum = race["scheduled_at"][:10] if race.get("scheduled_at") else ""
        datum = ".".join(reversed(datum.split("-"))) if datum else ""

        by_kat = {}
        for r in rows:
            bib = r["bib"].strip()
            if (slot, bib) in DROP:
                continue
            k = KATEGORIE_FIX.get((slot, bib)) or kat.get(bib, "ergebnis")
            by_kat.setdefault(k, []).append(r)

        wb = Workbook()
        wb.remove(wb.active)
        for k, krows in sorted(by_kat.items()):
            lizenz = k in LIZENZ
            ws = wb.create_sheet(k[:31] or "Ergebnis")
            ws["A1"] = "Sächsischer Radfahrer-Bund e. V."
            ws["A4"] = "Amtliches Ergebnis"
            ws["A6"] = args.titel
            ws["F7"] = f"{args.ort}, {datum}"
            ws["F8"] = "Ort, Datum"
            ws["A11"] = KLASSEN.get(k, k)
            hat_punkte = lizenz and k not in OHNE_PUNKTE
            if lizenz and hat_punkte:
                header = ["Platz", "St.-Nr.", "Name, Vorname", "Verein",
                          "UCI-ID", "Punkte", "Runden"]
            elif lizenz:
                # ohne Punktewertung: dafür Schlusszeit ausweisen
                header = ["Platz", "St.-Nr.", "Name, Vorname", "Verein",
                          "UCI-ID", "Zeit", "Runden"]
            else:
                header = ["Platz", "St.-Nr.", "Name, Vorname", "Verein",
                          "Zeit", "Runden"]
            for j, h in enumerate(header, 1):
                ws.cell(row=14, column=j, value=h)

            punkte = PUNKTE.get(k, {})
            gewertet, mit_status = [], []
            for idx, r in enumerate(krows):
                bib = r["bib"].strip()
                laps = RUNDEN_AMTLICH.get((slot, bib), int(r["laps"] or 0))
                status = r["status"].strip().upper()
                if not status and laps == 0 and not r["total_time_ms"].strip():
                    status = "DNS"
                if status == "DNS":
                    continue  # Nichtstarter erscheinen gar nicht (Wunsch 12.08.)
                if status:
                    mit_status.append((status, laps, r))
                else:
                    gewertet.append((laps, punkte.get(bib, 0),
                                     RANG_FIX.get((slot, bib), 50), idx, r))
            gewertet.sort(key=lambda t: (-t[1], -t[0], t[2], t[3]))
            sieger_runden = gewertet[0][0] if gewertet else ""
            # Renndistanz (Kerstin-Vorlage): 1-km-Runde -> Runden = km
            ws["D10"] = (f"{sieger_runden} Runden = {sieger_runden} km"
                         if sieger_runden != "" else "")

            out_row = 15
            platz = 0
            zeilen = ([(None, laps, pts, r) for laps, pts, _, _, r in gewertet]
                      + [(st, laps, None, r) for st, laps, r in mit_status])
            for status, laps, pts, r in zeilen:
                if status:
                    ws.cell(row=out_row, column=1, value=status)
                else:
                    platz += 1
                    ws.cell(row=out_row, column=1, value=platz)
                ws.cell(row=out_row, column=2,
                        value=int(r["bib"]) if r["bib"].isdigit() else r["bib"])
                ws.cell(row=out_row, column=3, value=r["name"].strip())
                rider = riders.get(r["bib"].strip(), {})
                ws.cell(row=out_row, column=4, value=rider.get("verein", ""))
                if lizenz and hat_punkte:
                    ws.cell(row=out_row, column=5, value=rider.get("uci_id", ""))
                    if pts is not None and k in PUNKTE:
                        ws.cell(row=out_row, column=6, value=pts)
                    ws.cell(row=out_row, column=7, value=laps)
                elif lizenz:
                    ws.cell(row=out_row, column=5, value=rider.get("uci_id", ""))
                    if r["total_time_ms"].strip():
                        c = ws.cell(row=out_row, column=6,
                                    value=int(r["total_time_ms"]) / 86400000.0)
                        c.number_format = "h:mm:ss"
                    ws.cell(row=out_row, column=7, value=laps)
                else:
                    if r["total_time_ms"].strip():
                        c = ws.cell(row=out_row, column=5,
                                    value=int(r["total_time_ms"]) / 86400000.0)
                        c.number_format = "h:mm:ss"
                    ws.cell(row=out_row, column=6, value=laps)
                out_row += 1
            # HINWEISE landen bewusst NUR im PDF (gesamt_pdf importiert das
            # Dict) — die Excel-Blätter bleiben clean für Kerstins Bearbeitung.
            formatiere(ws, len(header))
            print(f"  {k}: {len(zeilen)} Zeilen ({'Lizenz' if lizenz else 'Zeit'}-Format)")

        out = HERE / f"srb-{slugify(race['name'])}.xlsx"
        wb.save(out)
        print(f"-> {out}")

    req(base, "POST", f"/races/{orig}/activate")


if __name__ == "__main__":
    main()
