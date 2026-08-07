#!/usr/bin/env python3
"""Startnummern-PDF im A5.docx-Format: eine A5-Querseite pro vergebener Nummer.

Jede Nummer erscheint exakt so oft, wie sie in den zuweisung/*-anmeldeliste.csv
vorkommt (eine Seite pro Teilnehmer — Nummern, die in zwei Rennen vergeben
sind, z. B. Lauf 101 und Jedermann mittel 101, werden zweimal gedruckt).
Sortierung: Slot-Reihenfolge (Tagesplan), darin numerisch aufsteigend —
die Stapel kommen also rennfertig aus dem Drucker.

Layout wie /Users/jan/Downloads/A5.docx: A5 quer, KARLI-KRIT-Banner oben
mittig (13,5 x 2,2 cm), Nummer fett zentriert (~320 pt).

Usage: python3 gen_startnummern.py [--banner banner-strip.png] [--out PFAD.pdf]
"""
import argparse
import csv
from collections import Counter
from pathlib import Path

from reportlab.lib.pagesizes import A5, landscape
from reportlab.lib.units import cm
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
PAGE_W, PAGE_H = landscape(A5)  # 595 x 420 pt


def collect_numbers() -> list[tuple[str, int]]:
    """(slot, nummer)-Paare in Slot-, dann Nummern-Reihenfolge."""
    out = []
    for src in sorted((HERE / "zuweisung").glob("*-anmeldeliste.csv")):
        slot = src.stem.replace("-anmeldeliste", "")
        nums = []
        with open(src, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f, delimiter=";"):
                if row.get("nummer", "").strip():
                    nums.append(int(row["nummer"]))
        out.extend((slot, n) for n in sorted(nums))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--banner", default=None, help="Banner-Bild (PNG/JPEG)")
    ap.add_argument("--out", default=str(Path.home() / "Downloads/startnummern-karli-krit.pdf"))
    args = ap.parse_args()

    pairs = collect_numbers()
    print(f"{len(pairs)} Seiten aus {len(set(p[0] for p in pairs))} Slots")
    dupes = [n for n, c in Counter(n for _, n in pairs).items() if c > 1]
    if dupes:
        print(f"{len(dupes)} Nummern kommen mehrfach vor (Lauf/Rad-Überschneidung), "
              f"z. B. {sorted(dupes)[:6]}")

    c = canvas.Canvas(args.out, pagesize=(PAGE_W, PAGE_H))
    banner_w, banner_h = 13.5 * cm, 2.22 * cm
    for slot, num in pairs:
        if args.banner:
            c.drawImage(args.banner, (PAGE_W - banner_w) / 2,
                        PAGE_H - 0.75 * cm - banner_h,
                        width=banner_w, height=banner_h,
                        preserveAspectRatio=True, mask="auto")
        text = str(num)
        size = 320
        while c.stringWidth(text, "Helvetica-Bold", size) > PAGE_W - 1.2 * cm:
            size -= 10
        c.setFont("Helvetica-Bold", size)
        # Vertikal mittig im Bereich unter dem Banner (Kapitälchenhöhe ~0.72 em)
        area_top = PAGE_H - 0.75 * cm - banner_h - 0.4 * cm
        baseline = (area_top - 0.72 * size) / 2 + 0.06 * size
        c.drawCentredString(PAGE_W / 2, max(baseline, 0.8 * cm), text)
        c.showPage()
    c.save()
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
