#!/usr/bin/env python3
"""Fehlmessungs-Audit: Rundenzeiten-Anomalien über alle Rennen vom 08.08.

Liest tag_events read-only, zählt Überfahrten wie das Backend (15-s-Gate,
Start-Anker, Ende-Cutoff) und prüft pro Fahrer jede Rundenzeit gegen den
persönlichen Median:
    1,7-2,55x  -> vermutlich 1 Lesung verpasst
    2,55-3,55x -> vermutlich 2 Lesungen verpasst
    >3,55x     -> unklar (viele Misses oder Unterbrechung)
    <0,55x     -> Doppelzählung?
Zusätzlich (mit --xls): Runden-Abgleich gegen die offizielle Ergebnisliste
(St.-Nr. + Runden aus den zuordenbaren Sheets).

Ausgabe: fehlmessungs-audit.md + Konsole. Aggregiert auch pro Tag über den
Tag hinweg (schwache Tags = Hardware-Kandidaten).

Usage: e2e-venv/bin/python fehlmessungs_audit.py [--db PFAD] [--xls PFAD]
"""
import argparse
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
MIN_GAP_S = 15.0
LOCAL = timezone(timedelta(hours=2))  # deutsche Sommerzeit

# xls-Sheet -> Rennen-Namensfragment (nur eindeutig zuordenbare)
XLS_MAP = {
    "1.1 U15  1.2 U15 weibl": "U15", "1.3 U17 weibl (2)": "U15",
    "2.1 Masters 4": "U17m", "2.2 Jugend U17": "U17m",
    "3.1 Masters2 3.2 Master3 ": "Masters2", "3.3 Junioren U19": "Masters2",
    "4.1 Jedermann": "leicht", "4.2 jedermann mittel": "mittel",
    "4.3 Jedermann schwer": "schwer",
    "5.1Elite WT,CPT,CT, 5.2 U19 wei": "Frauen", "6.1 Jederfrau": "Frauen",
    "Fixie 1": "Quali", "Fixed B": "FLINTA",
}


def parse_ts(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def lokal(dt):
    return dt.astimezone(LOCAL).strftime("%H:%M:%S")


def fmt_s(s):
    return f"{int(s) // 60}:{int(s) % 60:02d} min"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(Path.home() / ".racetag/data/racetag.db"))
    ap.add_argument("--xls", default=str(HERE / "Karli Krit 08.08.26 Start und Ergebnislisten.xls"))
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    races = con.execute(
        "SELECT * FROM races WHERE started = 1 AND date(started_at) = '2026-08-08' "
        "ORDER BY started_at;").fetchall()

    xls_runden = {}  # rennen-fragment -> {bib: runden}
    try:
        import xlrd
        wb = xlrd.open_workbook(args.xls)
        for sheet, frag in XLS_MAP.items():
            try:
                s = wb.sheet_by_name(sheet)
            except Exception:
                continue
            header = [str(s.cell_value(13, j)).strip() for j in range(s.ncols)]
            r_col = next((j for j, h in enumerate(header) if h == "Runden"), None)
            if r_col is None:
                continue
            for i in range(14, s.nrows):
                try:
                    bib = int(float(s.cell_value(i, 1)))
                    laps = int(float(s.cell_value(i, r_col)))
                except (ValueError, TypeError):
                    continue
                xls_runden.setdefault(frag, {})[bib] = laps
    except Exception as exc:
        print(f"(xls-Abgleich übersprungen: {exc})")

    report = ["# Fehlmessungs-Audit Karli Krit 08.08.2026", ""]
    tag_issues = defaultdict(list)  # tag_id -> ["rennen: befund"]
    total_findings = 0

    for race in races:
        start = parse_ts(race["started_at"])
        ende = parse_ts(race["ended_at"]) if race["ended_at"] else None
        riders = {r["tag_id"]: r for r in con.execute(
            "SELECT tag_id, bib, name FROM riders WHERE race_id = ?;", (race["id"],))}

        passes = defaultdict(list)
        for row in con.execute(
                "SELECT tag_id, timestamp FROM tag_events "
                "WHERE race_id = ? AND event_type = 'arrive' ORDER BY timestamp, id;",
                (race["id"],)):
            ts = parse_ts(row["timestamp"])
            if ts < start or (ende and ts > ende):
                continue
            lst = passes[row["tag_id"]]
            anchor = lst[-1] if lst else start
            if (ts - anchor).total_seconds() < MIN_GAP_S:
                continue
            lst.append(ts)

        findings = []
        miss_events = []
        runde1_artefakte = 0
        frag = next((f for f in ["U15", "U17m", "Masters2", "leicht", "Quali",
                                 "Frauen", "mittel", "schwer", "FLINTA", "Lauf",
                                 "A Final"] if f.lower() in race["name"].lower()), None)
        offiziell = xls_runden.get(frag, {})

        streuner = []   # unregistrierte Tags / geparkte Plaketten an der Linie
        for tag_id, lst in sorted(passes.items(),
                                  key=lambda kv: len(kv[1]), reverse=True):
            rider = riders.get(tag_id)
            bib = rider["bib"] if rider else f"?({tag_id[-6:]})"
            name = (rider["name"] if rider else "").strip()
            laps = [(lst[i] - (lst[i - 1] if i else start)).total_seconds()
                    for i in range(len(lst))]
            if len(laps) < 3:
                continue
            med = statistics.median(laps)
            # Kein Teilnehmer: unregistriert ODER "Rundenzeit" unter einer
            # Minute (Tag liegt/steht im Lesefeld — Orga, Anmeldetisch, …)
            if rider is None or med < 60:
                streuner.append(f"{'unregistriert ' if rider is None else ''}"
                                f"Nr. {bib} {name}".strip()
                                + f" (…{tag_id[-6:]}): {len(lst)} Überfahrten, "
                                  f"Median {fmt_s(med)}")
                continue
            for i, lt in enumerate(laps):
                ratio = lt / med
                von = lst[i - 1] if i else start
                if i == 0 and not (0.55 <= ratio < 1.7):
                    # Runde 1 misst Start->erste Überfahrt: bei Massenstarts
                    # systematisch kürzer (Startüberfahrt) oder länger
                    # (neutralisierter Start) — KEIN Messfehler.
                    runde1_artefakte += 1
                    continue
                if 1.7 <= ratio < 2.55:
                    art = "1 Lesung verpasst"
                elif 2.55 <= ratio < 3.55:
                    art = "2 Lesungen verpasst"
                elif ratio >= 3.55:
                    art = "unklar (>=3 Misses oder Unterbrechung)"
                elif ratio < 0.55:
                    art = "Doppelzählung?"
                else:
                    continue
                miss_events.append({"bib": bib, "name": name, "runde": i + 1,
                                    "lt": lt, "ratio": ratio, "med": med,
                                    "von": von, "bis": lst[i], "art": art})
                tag_issues[tag_id].append(f"{race['name']}: {art}")

            off = offiziell.get(int(bib)) if str(bib).isdigit() else None
            if off is not None and off != len(lst):
                findings.append(
                    f"Nr. {bib} {name}: racetag {len(lst)} Runden, offizielle "
                    f"Liste {off} — DIFFERENZ {len(lst) - off:+d}")

        # Cluster-Erkennung: >=3 Fahrer mit Miss-Fenstern in derselben Minute
        # = systemischer Aussetzer (Reader/Antenne/Kollisionen), kein Tag-Problem
        minute_hits = defaultdict(set)
        for e in miss_events:
            t0 = int(e["von"].timestamp() // 60)
            t1 = int(e["bis"].timestamp() // 60)
            for t in range(t0, t1 + 1):
                minute_hits[t].add(e["bib"])
        cluster_minuten = sorted(t for t, bibs in minute_hits.items()
                                 if len(bibs) >= 3)
        cluster = []
        for t in cluster_minuten:
            if cluster and t == cluster[-1][1] + 1:
                cluster[-1] = (cluster[-1][0], t)
            else:
                cluster.append((t, t))
        for a, b in cluster:
            bibs = set()
            for t in range(a, b + 1):
                bibs |= minute_hits[t]
            von = datetime.fromtimestamp(a * 60, tz=timezone.utc)
            bis = datetime.fromtimestamp((b + 1) * 60, tz=timezone.utc)
            findings.insert(0, f"⚠ SYSTEMISCH: {lokal(von)}-{lokal(bis)} — "
                               f"{len(bibs)} Fahrer gleichzeitig betroffen "
                               f"(Reader/Antenne/Pulk-Kollisionen)")
        for e in miss_events:
            findings.append(
                f"Nr. {e['bib']} {e['name']}: Runde {e['runde']} = {fmt_s(e['lt'])} "
                f"({e['ratio']:.1f}x Median {fmt_s(e['med'])}) "
                f"{lokal(e['von'])}-{lokal(e['bis'])} — {e['art']}")

        line = (f"## {race['name']} — {len(passes)} Tags, "
                f"Start {lokal(start)}" + (f", Ende {lokal(ende)}" if ende else ""))
        report.append(line)
        if runde1_artefakte:
            report.append(f"(Runde-1-Artefakte ausgeblendet: {runde1_artefakte} — "
                          f"Startüberfahrt/neutralisierter Start bei Massenstart)")
        if offiziell:
            report.append(f"(xls-Abgleich: {len(offiziell)} Nummern)")
        if findings:
            report.extend(f"- {f}" for f in findings)
            total_findings += len(findings)
        else:
            report.append("- keine Auffälligkeiten")
        if streuner:
            report.append(f"- ℹ nicht gewertet (Dauerleser an der Linie): "
                          f"{len(streuner)}")
            report.extend(f"  - {s}" for s in streuner)
        report.append("")

    schwache = {t: v for t, v in tag_issues.items() if len(v) >= 2}
    report.append("## Tags mit Befunden in MEHREREN Runden/Rennen (Hardware-Kandidaten)")
    if schwache:
        for t, v in sorted(schwache.items(), key=lambda kv: -len(kv[1])):
            report.append(f"- …{t[-8:]}: {len(v)} Befunde ({'; '.join(v[:4])})")
    else:
        report.append("- keine")

    out = HERE / "fehlmessungs-audit.md"
    out.write_text("\n".join(report), encoding="utf-8")
    print("\n".join(report))
    print(f"\n{total_findings} Befunde gesamt -> {out}")


if __name__ == "__main__":
    main()
