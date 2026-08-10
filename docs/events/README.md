# Event-Ordner — Konvention

Ein Ordner pro Event: `docs/events/<JJJJ-MM-TT>-<event>/`.

**Getrackt** (Git): Runbooks, Anleitungen, Skripte, Assets (z. B. Banner).
**Lokal** (via `.gitignore` in diesem Ordner): alles mit Personennamen —
Meldelisten, Zuweisungen, Ergebnis-Dateien, racetag-Exporte, Fotos, xls/xlsx.

Struktur am Beispiel `2026-08-08-karli-krit/` (dient als Vorlage fürs nächste
Event — Ordner kopieren, Zirkel/Slots in den Skripten anpassen):

- `RACEDAY-*.md` / `SPICKZETTEL.md` / `ANLEITUNG-*.md` — Runbook & Co.
- `*.py` — Event-Werkzeuge (Rennen anlegen, Meldelisten ziehen, Nummern
  zuweisen, Startnummern-PDFs, Prüfläufe, Auswertungen, SRB-Export,
  Gesamt-PDF). Kommandos laufen aus dem Repo-Root oder dem Event-Ordner;
  Skripte finden ihre Daten relativ zur eigenen Datei.
- `rennen/` — Meldelisten vom Portal (lokal)
- `zuweisung/` — Nummern-Zuweisungen & Anmeldelisten (lokal)
- `exporte/` — racetag-CSV-Exporte vom Renntag (lokal)
- Ergebnis-Artefakte (`srb-*.xlsx`, `ergebnis-*.csv`, Audit-Reports) — lokal
